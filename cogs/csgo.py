import aiohttp
import asyncio
import checks
import datetime
import time
import discord
import json
import os
import socket
import traceback
from rcon.source import rcon
from a2s import info

from bot import Discord_10man
from collections import Counter
from databases import Database
from datetime import datetime
from discord.ext import commands, tasks
from random import choice, shuffle, randint
from steam.steamid import SteamID
from typing import List
from utils.csgo_server import CSGOServer
from utils.veto_image import VetoImage
from unidecode import unidecode

import logging
from logging.config import fileConfig
import pprint

# TODO: Allow administrators to update the maplist
active_map_pool = ['de_inferno', 'de_train', 'de_mirage', 'de_nuke', 'de_overpass', 'de_dust2', 'de_vertigo']
reserve_map_pool = ['de_cache', 'de_cbble', 'cs_office', 'cs_agency']
current_map_pool = active_map_pool.copy()

emoji_bank = ['0️⃣', '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']

# Veto style 1 2 2 2 1 1 1, last two 1s are for if we are playing with coaches


EU_ISO = ['AT', 'BE', 'BG', 'HR', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'NL',
          'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE']

CIS_ISO = ['BY', 'KZ', 'RU', 'UA']


class CSGO(commands.Cog):
    def __init__(self, bot: Discord_10man, veto_image):
        fileConfig('logging.conf')
        self.logger = logging.getLogger(f'10man.{__name__}')
        self.logger.debug(f'Loaded {__name__}')

        self.bot: Discord_10man = bot
        self.veto_image = veto_image
        self.readied_up: bool = False

    @commands.command(hidden=True)
    async def test(self, ctx: commands.Context, *args):
        self.logger.debug(f'{ctx.author}: {ctx.prefix}{ctx.invoked_with} {ctx.args[2:]}')
        print(f'test')

    @commands.command(aliases=['10man', 'setup'],
                      help='This command takes the users in a voice channel and selects two random '
                           'captains. It then allows those captains to select the members of their '
                           'team in a 1 2 2 2 1 fashion. It then configures the server with the '
                           'correct config.', brief='Helps automate setting up a PUG')
    @commands.check(checks.voice_channel)
    @commands.check(checks.match_size_check)
    @commands.check(checks.linked_accounts)
    @commands.check(checks.available_server)
    async def pug(self, ctx: commands.Context, *args):
        self.logger.debug(f'{ctx.author}: {ctx.prefix}{ctx.invoked_with} {ctx.args[2:]}')
        random_teams: bool = False
        map_arg: str = None
        team1_captain_arg: discord.Member = None
        team2_captain_arg: discord.Member = None
        for arg in args:
            if arg == 'random':
                random_teams = True
                self.logger.debug('Random Teams Enabled')
            elif arg in current_map_pool:
                map_arg = arg
                self.logger.debug(f'Force Selected Map = {map_arg}')
            else:
                member: discord.Member = await commands.MemberConverter().convert(ctx, arg)
                if member in ctx.author.voice.channel.members:
                    if team1_captain_arg is None:
                        team1_captain_arg = member
                        self.logger.debug(f'Forced Team 1 Captain = {team1_captain_arg}')
                    elif team2_captain_arg is None and member is not team1_captain_arg:
                        team2_captain_arg = member
                        self.logger.debug(f'Forced Team 2 Captain = {team2_captain_arg}')
                    else:
                        if member is team1_captain_arg:
                            raise commands.CommandError(message=f'One user cannot be captain of 2 teams.')
                        else:
                            raise commands.CommandError(message=f'You can only set 2 captains.')
                else:
                    raise commands.CommandError(message=f'Invalid Argument: `{arg}`')

        if not self.pug.enabled:
            self.logger.info('Pug called from queue as pug is disabled')
            if len(self.bot.queue_captains) > 0:
                team1_captain_arg = self.bot.queue_captains.pop(0)
                self.logger.debug(f'Forced Team 1 Captain = {team1_captain_arg}')
            if len(self.bot.queue_captains) > 0:
                team2_captain_arg = self.bot.queue_captains.pop(0)
                self.logger.debug(f'Forced Team 2 Captain = {team2_captain_arg}')

        # TODO: Refactor this mess
        db = Database('sqlite:///main.sqlite')
        await db.connect()
        csgo_server = self.bot.servers[0]
        for server in self.bot.servers:
            if server.available:
                server.available = False
                csgo_server = server
                break
        channel_original = ctx.author.voice.channel
        players: List[discord.Member] = ctx.author.voice.channel.members.copy()
        players = players[:self.bot.match_size]
        if self.bot.dev:
            players = [ctx.author] * 10
            self.logger.info('Filling list of players with the message author because bot is in dev mode')

        if random_teams:
            shuffle(players)
            team1 = players[:len(players) // 2]
            team2 = players[len(players) // 2:]
            team1_captain = team1[0]
            team2_captain = team2[0]
            message_text = 'Random Teams'
            message = await ctx.send(message_text)
            embed = self.player_veto_embed(message_text=message_text, players_text='Random Teams', team1=team1,
                                           team1_captain=team1_captain, team2=team2, team2_captain=team2_captain)
            await message.edit(content=message_text, embed=embed)
            self.logger.debug(f'Random Team1: {team1}')
            self.logger.debug(f'Random Team2: {team2}')
        else:
            emojis = emoji_bank.copy()
            del emojis[len(players) - 2:len(emojis)]
            emojis_selected = []
            team1 = []
            team2 = []
            if team1_captain_arg is not None:
                team1_captain = team1_captain_arg
            else:
                team1_captain = players[randint(0, len(players) - 1)]
            self.logger.debug(f'team1_captain = {team1_captain}')
            team1.append(team1_captain)
            players.remove(team1_captain)

            if team2_captain_arg is not None:
                team2_captain = team2_captain_arg
            else:
                team2_captain = players[randint(0, len(players) - 1)]
            self.logger.debug(f'team2_captain = {team1_captain}')
            team2.append(team2_captain)
            players.remove(team2_captain)

            current_team_player_select = 1

            current_captain = team1_captain
            player_veto_count = 0

            message = await ctx.send(f'{self.bot.match_size} man time\nLoading player selection...')
            for emoji in emojis:
                await message.add_reaction(emoji)

            emoji_remove = []

            player_veto = []
            if self.bot.match_size == 2:
                player_veto = [1, 1]
            for i in range(self.bot.match_size - 2):
                if i == 0 or i == self.bot.match_size - 3:
                    player_veto.append(1)
                elif i % 2 == 0:
                    player_veto.append(2)
            self.logger.debug(f'player_veto = {player_veto}')

            while len(players) > 0:
                message_text = ''
                players_text = ''

                if current_team_player_select == 1:
                    message_text += f'<@{team1_captain.id}>'
                    current_captain = team1_captain
                elif current_team_player_select == 2:
                    message_text += f'<@{team2_captain.id}>'
                    current_captain = team2_captain
                self.logger.debug(f'current_captain (captain currently selected) = {current_captain}')

                message_text += f' select {player_veto[player_veto_count]}\n'
                message_text += 'You have 60 seconds to choose your player(s)\n'

                i = 0
                for player in players:
                    players_text += f'{emojis[i]} - <@{player.id}>\n'
                    i += 1
                embed = self.player_veto_embed(message_text=message_text, players_text=players_text, team1=team1,
                                               team1_captain=team1_captain, team2=team2, team2_captain=team2_captain)
                await message.edit(content=message_text, embed=embed)
                if len(emoji_remove) > 0:
                    for emoji in emoji_remove:
                        await message.clear_reaction(emoji)
                    emoji_remove = []

                selected_players = 0
                seconds = 0
                while True:
                    await asyncio.sleep(1)
                    message = await ctx.fetch_message(message.id)

                    for reaction in message.reactions:
                        users = [user async for user in reaction.users()]
                        if current_captain in users and selected_players < player_veto[player_veto_count] and not (
                                reaction.emoji in emojis_selected):
                            index = emojis.index(reaction.emoji)
                            if current_team_player_select == 1:
                                team1.append(players[index])
                            if current_team_player_select == 2:
                                team2.append(players[index])
                            self.logger.debug(f'{current_captain} selected {players[index]}')
                            emojis_selected.append(reaction.emoji)
                            emoji_remove.append(reaction.emoji)
                            del emojis[index]
                            del players[index]
                            selected_players += 1

                    seconds += 1

                    if seconds % 60 == 0:
                        for _ in range(0, player_veto[player_veto_count]):
                            index = randint(0, len(players) - 1)
                            self.logger.debug(f'{current_captain} selected {players[index]}')
                            if current_team_player_select == 1:
                                team1.append(players[index])
                            if current_team_player_select == 2:
                                team2.append(players[index])
                            emojis_selected.append(emojis[index])
                            del emojis[index]
                            del players[index]
                            selected_players += 1

                    if selected_players == player_veto[player_veto_count]:
                        if current_team_player_select == 1:
                            current_team_player_select = 2
                        elif current_team_player_select == 2:
                            current_team_player_select = 1
                        break

                player_veto_count += 1

        if map_arg is None:
            message_text = 'Map Veto Loading'
        else:
            message_text = f'Map is `{map_arg}`'
        players_text = 'None'
        embed = self.player_veto_embed(message_text=message_text, players_text=players_text, team1=team1,
                                       team1_captain=team1_captain, team2=team2, team2_captain=team2_captain)
        await message.edit(content=message_text, embed=embed)
        await message.clear_reactions()

        if map_arg is not None:
            chosen_map_embed = await self.get_chosen_map_embed(map_arg)
            await ctx.send(embed=chosen_map_embed)

        team1_steamIDs = {}
        team2_steamIDs = {}
        spectator_steamIDs = {}

        if ctx.author.voice.channel.category is None:
            team1_channel = await ctx.guild.create_voice_channel(name=f'team_{team1_captain.display_name}',
                                                                 user_limit=int(self.bot.match_size / 2) + 1)
            team2_channel = await ctx.guild.create_voice_channel(name=f'team_{team2_captain.display_name}',
                                                                 user_limit=int(self.bot.match_size / 2) + 1)
        else:
            team1_channel = await ctx.author.voice.channel.category.create_voice_channel(
                name=f'team_{team1_captain.display_name}', user_limit=int(self.bot.match_size / 2) + 1)
            team2_channel = await ctx.author.voice.channel.category.create_voice_channel(
                name=f'team_{team2_captain.display_name}', user_limit=int(self.bot.match_size / 2) + 1)

        for player in team1:
            await player.move_to(channel=team1_channel, reason=f'You are on {team1_captain}\'s Team')
            data = await db.fetch_one('SELECT steam_id FROM users WHERE discord_id = :player',
                                      {"player": str(player.id)})
            team1_steamIDs[data[0]] = unidecode(player.display_name)
        self.logger.debug(f'Moved all team1 players to {team1_channel}')

        for player in team2:
            await player.move_to(channel=team2_channel, reason=f'You are on {team2_captain}\'s Team')
            data = await db.fetch_one('SELECT steam_id FROM users WHERE discord_id = :player',
                                      {"player": str(player.id)})
            team2_steamIDs[data[0]] = unidecode(player.display_name)
        self.logger.debug(f'Moved all team2 players to {team2_channel}')

        if len(self.bot.spectators) > 0:
            for spec in self.bot.spectators:
                data = await db.fetch_one('SELECT steam_id FROM users WHERE discord_id = :spectator',
                                          {"spectator": str(spec.id)})
                spectator_steamIDs[data[0]] = unidecode(spec.display_name)
            self.logger.info('Added Spectators')

        if map_arg is None:
            map_list = await self.map_veto(ctx, team1_captain, team2_captain)
        else:
            map_list = [map_arg]

        bot_ip = self.bot.web_server.IP
        if self.bot.bot_IP != '':
            bot_ip = self.bot.bot_IP

        team1_country = 'IE'
        team2_country = 'IE'

        team1_flags = []
        team2_flags = []

        team1_flag_request = ''
        team2_flag_request = ''

        for player in team1_steamIDs:
            team1_flag_request += str(SteamID(player).as_64) + ','
        team1_flag_request = team1_flag_request[:-1]

        for player in team2_steamIDs:
            team2_flag_request += str(SteamID(player).as_64) + ','
        team2_flag_request = team2_flag_request[:-1]

        self.logger.info('Making request to the Steam API to get player flags')
        session = aiohttp.ClientSession()
        async with session.get(f'https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/'
                               f'?key={self.bot.steam_web_api_key}'
                               f'&steamids={team1_flag_request}') as resp:
            player_info = await resp.json()
            for player in player_info['response']['players']:
                if 'loccountrycode' in player:
                    team1_flags.append(player['loccountrycode'])
            await session.close()

        session = aiohttp.ClientSession()
        async with session.get(f'https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/'
                               f'?key={self.bot.steam_web_api_key}'
                               f'&steamids={team2_flag_request}') as resp:
            player_info = await resp.json()
            for player in player_info['response']['players']:
                if 'loccountrycode' in player:
                    team2_flags.append(player['loccountrycode'])
            await session.close()

        # TODO: Add check for EU/CIS flag
        if len(team1_flags) > 0:
            team1_country = Counter(team1_flags).most_common(1)[0][0]
        if len(team2_flags) > 0:
            team2_country = Counter(team2_flags).most_common(1)[0][0]

        team1_name = f'team_{unidecode(team1_captain.display_name)}'
        team2_name = f'team_{unidecode(team2_captain.display_name)}'

        
        match_id = time.time().__int__()

        match_config = {
            'matchid': match_id,
            'num_maps': 1,
            'maplist': map_list,
            'skip_veto': True,
            'veto_first': 'team1',
            'side_type': 'always_knife',
            'players_per_team': int(self.bot.match_size / 2),
            'min_players_to_ready': 1,
            'spectators': {
                'players': spectator_steamIDs,
            },
            'team1': {
                'name': team1_name,
                'tag': 'team1',
                'flag': team1_country,
                'players': team1_steamIDs
            },
            'team2': {
                'name': team2_name,
                'tag': 'team2',
                'flag': team2_country,
                'players': team2_steamIDs
            },
            'cvars': {
                'matchzy_remote_log_url': f'http://{bot_ip}:{self.bot.web_server.port}/',
                'matchzy_enable_damage_report': '1',
            }
        }

        self.logger.debug(f'Match Config =\n {pprint.pformat(match_config)}')

        with open(f'./{match_id}.json', 'w') as outfile:
            json.dump(match_config, outfile, ensure_ascii=False, indent=4)

        await ctx.send('If you are coaching, once you join the server, type .coach')
        loading_map_message = await ctx.send('Server is being configured')
        await asyncio.sleep(10)
        await loading_map_message.delete()
        load_match = await rcon(f'matchzy_loadmatch_url "http://{bot_ip}:{self.bot.web_server.port}/{match_id}"',
                                host=csgo_server.server_address, port=csgo_server.server_port, passwd=csgo_server.RCON_password)
        self.logger.debug(f'Load Match via URL\n {load_match}')
        await asyncio.sleep(5)
        connect_embed = await self.connect_embed(csgo_server)
        if self.bot.connect_dm:
            for player in team1 + team2 + self.bot.spectators:
                try:
                    await player.send(embed=connect_embed)
                except (discord.HTTPException, discord.Forbidden):
                    await ctx.send(f'Unable to PM <@{player.id}> the server details.')
                    self.logger.warning(f'{player} was not sent the IP via DM')
        else:
            await ctx.send(embed=connect_embed)
        score_embed = discord.Embed()
        score_embed.add_field(name='0', value=team1_name, inline=True)
        score_embed.add_field(name='0', value=team2_name, inline=True)
        score_message = await ctx.send('Match in Progress', embed=score_embed)

        csgo_server.get_context(ctx=ctx, channels=[channel_original, team1_channel, team2_channel],
                                players=team1 + team2, score_message=score_message)
        csgo_server.set_team_names([team1_name, team2_name])
        self.bot.web_server.add_server(csgo_server)

        if not self.pug.enabled:
            self.queue_check.start()
            self.logger.info('Queue Starting Back')

    @pug.error
    async def pug_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandError):
            await ctx.send(str(error))
            self.logger.warning(str(error))
        self.logger.exception(f'{ctx.command} caused an exception')

    def player_veto_embed(self, message_text, players_text, team1, team1_captain, team2, team2_captain):
        team1_text = ''
        team2_text = ''
        for team1_player in team1:
            team1_text += f'<@{team1_player.id}>'
            if team1_player is team1_captain:
                team1_text += ' 👑'
            team1_text += '\n'
        for team2_player in team2:
            team2_text += f'<@{team2_player.id}>'
            if team2_player is team2_captain:
                team2_text += ' 👑'
            team2_text += '\n'

        embed = discord.Embed()
        embed.add_field(name=f'Team {team1_captain.display_name}', value=team1_text, inline=True)
        embed.add_field(name='Players', value=players_text, inline=True)
        embed.add_field(name=f'Team {team2_captain.display_name}', value=team2_text, inline=True)
        return embed

    async def get_chosen_map_embed(self, chosen_map, session=aiohttp.ClientSession()):
        ''' Returns a :class:`discord.Embed` which contains an image of
        the map chosen on completion of the veto. closes the session passed.

        Parameters
        -----------
        chosen_map: :class:`str`
            The chosen map name string
        session: :class:`aiohttp.ClientSession`
            Current aiohttp client session
        '''
        if session.closed:
            session = aiohttp.ClientSession()
        veto_image_fp = 'result.png'
        base_url = f'http://{self.bot.bot_IP}:{self.bot.web_server.port}'

        chosen_map_file_name = chosen_map + self.veto_image.image_extension
        chosen_map_fp = os.path.join(
            self.veto_image.map_images_fp, chosen_map_file_name)
        percentage = 0.25
        VetoImage.resize(chosen_map_fp, percentage, output_fp=veto_image_fp)
        response = await session.get(f'{base_url}/map-veto')
        path = (await response.json())['path']
        chosen_map_image_url = base_url + path
        map_chosen_embed = discord.Embed(title=f'The chosen map is ```{chosen_map}```',
                                         color=discord.Colour(0x650309))
        map_chosen_embed.set_image(url=chosen_map_image_url)
        await session.close()

        return map_chosen_embed

    async def map_veto(self, ctx: commands.Context, team1_captain, team2_captain):
        '''Returns :class:`list` of :class:`str` which is the remaining map
        after the veto

        Embed image updates as the maps are vetoed. The team captains can
        veto a map by reacting to the map number to be vetoed

        Parameters
        -----------
        ctx: :class:`discord.Context`
            The context object provided
        team1_captain: :class:`discord.Member`
            One of the team captains
        team2_captain: :class:`discord.Member`
            The other team captain
        '''

        veto_image_fp = 'result.png'
        session = aiohttp.ClientSession()
        base_url = f'http://{self.bot.bot_IP}:{self.bot.web_server.port}'

        async def get_embed(current_team_captain):
            ''' Returns :class:`discord.Embed` which contains the map veto
            image and the current team captain who has to make a veto

            Parameters
            -----------
            current_team_captain: :class:`discord.Member`
                The current team captain
            '''
            embed = discord.Embed(title='__Map veto__',
                                  color=discord.Colour(0x650309))
            embed.set_image(url="attachment://veto.png")
            embed.set_footer(text=f'It is now {current_team_captain}\'s turn to veto | You have 60 seconds',
                             icon_url=current_team_captain.display_avatar.url)
            return embed

        async def add_reactions(message, num_maps):
            ''' Adds the number emoji reactions to the message. This is used
            to select the veto map

            Parameters
            -----------
            message: :class:`discord.Message`
                The veto message to add the number emoji reactions to
            num_maps: :class:`int`
                The number of maps there are to chose from
            '''

            for index in range(1, num_maps + 1):
                await message.add_reaction(emoji_bank[index])

        async def get_next_map_veto(message, current_team_captain, is_vetoed):
            ''' Obtains the next map which was vetoed

            Parameters
            -----------
            message: :class:`discord.Message`
                The veto message which has the number emoji reactions
            num_maps: :class:`discord.Member`
                The current team captain
            '''

            check = lambda reaction, user: reaction.emoji in emoji_bank and user == current_team_captain
            index = -1
            try:
                (reaction, _) = await self.bot.wait_for('reaction_add', check=check, timeout=60.0)
            except asyncio.TimeoutError:
                validIndexes = [i for i in range(len(is_vetoed)) if not is_vetoed[i]]
                index = choice(validIndexes)
                self.logger.debug('Force selected Map')
            else:
                index = emoji_bank.index(reaction.emoji) - 1

            return map_list[index]

        map_list = current_map_pool.copy()
        is_vetoed = [False] * len(map_list)
        num_maps_left = len(map_list)
        current_team_captain = choice((team1_captain, team2_captain))

        self.veto_image.construct_veto_image(map_list, veto_image_fp,
                                             is_vetoed=is_vetoed, spacing=25)
        embed = await get_embed(current_team_captain)
        response = await session.get(f'{base_url}/map-veto')
        path = (await response.json())['path']
        url = base_url.rstrip('/') + '/' + path.lstrip('/')
        self.logger.debug(f'Veto image URL: {url}')
        image_response = await session.get(url)
        image_data = await image_response.read()
        with open('veto_image.png', 'wb') as f:
            f.write(image_data)
        file = discord.File('veto_image.png', 'veto.png')
        message = await ctx.send(file=file, embed=embed)

        await add_reactions(message, len(map_list))

        while num_maps_left > 1:
            message = await ctx.fetch_message(message.id)

            map_vetoed = await get_next_map_veto(message, current_team_captain, is_vetoed)
            self.logger.debug(f'{current_team_captain} vetoed {map_vetoed}')
            vetoed_map_index = map_list.index(map_vetoed)
            is_vetoed[vetoed_map_index] = True

            if current_team_captain == team1_captain:
                current_team_captain = team2_captain
            else:
                current_team_captain = team1_captain

            self.veto_image.construct_veto_image(map_list, veto_image_fp,
                                                 is_vetoed=is_vetoed, spacing=25)
            embed = await get_embed(current_team_captain)
            await asyncio.gather(message.edit(embed=embed),
                                 message.clear_reaction(emoji_bank[vetoed_map_index + 1]))

            num_maps_left -= 1

        map_list = list(filter(lambda map_name: not is_vetoed[map_list.index(map_name)], map_list))

        chosen_map = map_list[0]
        self.logger.debug(f'Chosen map {chosen_map}')
        chosen_map_embed = await self.get_chosen_map_embed(chosen_map, session)
        await asyncio.gather(message.clear_reactions(),
                             message.edit(embed=chosen_map_embed))
        return map_list

    @tasks.loop(seconds=5.0)
    async def queue_check(self):
        db = Database('sqlite:///main.sqlite')
        await db.connect()
        not_connected_members = []
        for member in self.bot.queue_voice_channel.members:
            data = await db.fetch_one('SELECT 1 FROM users WHERE discord_id = :member', {"member": str(member.id)})
            if data is None:
                not_connected_members.append(member)
                await member.move_to(channel=None, reason=f'Please link your account with .link <Steam Profile URL>')

        if len(not_connected_members) > 0:
            error_message = ''
            for member in not_connected_members:
                error_message += f'<@{member.id}> '
            error_message += f'must connect their steam account with the command `{self.bot.command_prefix}link <Steam Profile URL>`'
            await self.bot.queue_ctx.send(error_message)
            self.logger.debug('Members in the queue did not connect their account')
            self.logger.info(error_message)

        await db.disconnect()
        available: bool = False
        for server in self.bot.servers:
            if server.available:
                available = True
                break
        if (len(self.bot.queue_voice_channel.members) >= self.bot.match_size or (
                self.bot.dev and len(self.bot.queue_voice_channel.members) >= 1)) and available:
            embed = discord.Embed()
            embed.add_field(name='You have 60 seconds to ready up!', value='Ready: ✅', inline=False)
            ready_up_message = await self.bot.queue_ctx.send(embed=embed)
            await ready_up_message.add_reaction('✅')
            self.ready_up.start(message=ready_up_message, members=self.bot.queue_voice_channel.members)
            self.bot.users_not_ready = self.bot.queue_voice_channel.members
            self.queue_check.stop()
            self.logger.debug(f'Unready users {self.bot.users_not_ready}')

    @tasks.loop(seconds=1.0, count=60)
    async def ready_up(self, message: discord.Message, members: List[discord.Member]):
        message = await self.bot.queue_ctx.fetch_message(message.id)

        # TODO: Add check for only the first self.bot.match_size users
        check_emoji = None
        for reaction in message.reactions:
            if reaction.emoji == '✅':
                check_emoji = reaction
                break

        user_reactions = await check_emoji.users().flatten()
        ready = True
        for member in members:
            if member not in user_reactions:
                ready = False
            else:
                if member in self.bot.users_not_ready:
                    self.bot.users_not_ready.remove(member)

        if ready:
            self.readied_up = True
            self.ready_up.stop()

    @ready_up.after_loop
    async def ready_up_cancel(self):
        if self.readied_up:
            self.readied_up = False
            self.logger.debug(f'Queue users {self.bot.queue_voice_channel.members}')
            self.logger.info('Starting Pug Command')
            await self.pug(self.bot.queue_ctx)
        else:
            not_ready_text: List[str] = []
            for member in self.bot.users_not_ready:
                not_ready_text.append(f'<@{member.id}>')
                await member.move_to(None, reason='You did not ready up')
            await self.bot.queue_ctx.send(f'{", ".join(map(str, not_ready_text))} did not ready up')
            self.logger.debug('Users did not ready up')
            self.bot.users_not_ready = []
            self.queue_check.start()

    @commands.command(help='This command creates a URL that people can click to connect to the server.',
                      brief='Creates a URL people can connect to', usage='<ServerID>', hidden=True)
    async def connect(self, ctx: commands.Context, server_id: int = 0):
        self.logger.debug(f'{ctx.author}: {ctx.prefix}{ctx.invoked_with} {ctx.args[2:]}')
        embed = await self.connect_embed(self.bot.servers[server_id])
        if self.bot.connect_dm:
            try:
                await ctx.author.send(embed=embed)
            except (discord.HTTPException, discord.Forbidden):
                await ctx.send(f'Unable to PM <@{ctx.author.id}> the server details.')
                self.logger.warning(f'{ctx.author} was not sent the IP via DM')
        else:
            await ctx.send(embed=embed)

    @connect.error
    async def connect_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error.__cause__, valve.source.NoResponseError) or isinstance(error.__cause__, socket.gaierror):
            embed = discord.Embed(color=0xff0000)
            embed.add_field(name="Cannot Connect to Server", value="No Response from Server", inline=False)
            await ctx.send(embed=embed)
            self.logger.error('Cannot Connect to Server, No Response from Server')
        elif isinstance(error.__cause__, IndexError):
            embed = discord.Embed(color=0xff0000)
            embed.add_field(name="Cannot Connect to Server", value="Not valid Server ID", inline=False)
            await ctx.send(embed=embed)
            self.logger.warning('Not valid Server ID')
        self.logger.exception(f'{ctx.command} caused an exception')

    async def connect_embed(self, csgo_server: CSGOServer) -> discord.Embed:
        embed = discord.Embed(title="PUG Server", color=0xf4c14e)
        embed.set_thumbnail(
            url="https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/730/69f7ebe2735c366c65c0b33dae00e12dc40edbe4.jpg")
        embed.add_field(name='Quick Connect',
                        value=f'steam://connect/{csgo_server.server_address}:{csgo_server.server_port}/{csgo_server.server_password}',
                        inline=False)
        embed.add_field(name='Console Connect',
                        value=f'connect {csgo_server.server_address}:{csgo_server.server_port}; password {csgo_server.server_password}',
                        inline=False)
        return embed

    @commands.command(aliases=['maps'], help='Resets the map pool to be whatever maps are specified'
                                             'Must have odd number of maps. Use "active" or "reserve" for the respective map pools.',
                      brief='Changes map pool', usage='<lists of maps> or "active" or "reserve"')
    @commands.has_permissions(administrator=True)
    async def map_pool(self, ctx: commands.Context, *args):
        self.logger.debug(f'{ctx.author}: {ctx.prefix}{ctx.invoked_with} {ctx.args[2:]}')
        global current_map_pool
        current_map_pool = []
        for arg in args:
            if arg == 'active':
                current_map_pool += active_map_pool
            elif arg == 'reserve':
                current_map_pool += reserve_map_pool
            else:
                if os.path.isfile(f'images/map_images/{arg}.png'):
                    if arg not in current_map_pool:
                        current_map_pool.append(arg)
                    else:
                        raise commands.CommandError(message=f'`{arg}` is already in the map pool.')
                else:
                    raise commands.CommandError(message=f'`{arg}` does not have an image in `/images/map_images/'
                                                        'and thus cannot be added to the map pool.\n'
                                                        'Please put an image in that folder to continue.')
        logging.info(f'Current map pool: {current_map_pool}')

    @map_pool.error
    async def map_pool_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandError):
            await ctx.send(str(error))
            self.logger.warning(str(error))
        self.logger.exception(f'{ctx.command} caused an exception')

    @commands.command(aliases=['live', 'live_matches'], help='This command shows the current live matches.',
                      brief='Shows the current live matches')
    @commands.check(checks.active_game)
    async def matches(self, ctx: commands.Context):
        self.logger.debug(f'{ctx.author}: {ctx.prefix}{ctx.invoked_with} {ctx.args[2:]}')
        for server in self.bot.servers:
            if not server.available:
                score_embed = discord.Embed(color=0x00ff00)
                score_embed.add_field(name=f'{server.team_scores[0]}',
                                      value=f'{server.team_names[0]}', inline=True)
                score_embed.add_field(name=f'{server.team_scores[1]}',
                                      value=f'{server.team_names[1]}', inline=True)
                gotv = server.get_gotv()
                if gotv is None:
                    score_embed.add_field(name='GOTV',
                                          value='Not Configured',
                                          inline=False)
                else:
                    score_embed.add_field(name='GOTV',
                                          value=f'connect {server.server_address}:{gotv}',
                                          inline=False)
                score_embed.set_footer(text="🟢 Live")
                await ctx.send(embed=score_embed)

    @matches.error
    async def matches_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandError):
            await ctx.send(str(error))
            self.logger.warning(str(error))
        self.logger.exception(f'{ctx.command} caused an exception')


async def setup(client):
    veto_image_generator = VetoImage('images/map_images', 'images/x.png', 'png')
    await client.add_cog(CSGO(client, veto_image_generator))
