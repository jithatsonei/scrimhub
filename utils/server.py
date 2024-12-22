import discord
import logging
import os
import pprint
import socket
import traceback
import uuid
import valve.rcon

from aiohttp import web
from json import JSONDecodeError
from logging.config import fileConfig
from typing import List, Union
from utils.csgo_server import CSGOServer


class WebServer:
    def __init__(self, bot):
        from bot import Discord_10man

        fileConfig('logging.conf')
        self.logger = logging.getLogger(f'10man.{__name__}')

        self.bot: Discord_10man = bot
        self.IP: str = "172.21.73.129"
        self.port: int = self.bot.bot_port
        self.site: web.TCPSite = None
        self.csgo_servers: List[CSGOServer] = []
        self.map_veto_image_path = self.create_new_veto_filepath()

    def create_new_veto_filepath(self):
        path = f'/map-veto/{str(uuid.uuid1())}'
        return path

    async def _handler(self, request: web.Request) -> Union[web.Response, web.FileResponse]:
        """
        Super simple HTTP handler.
        Parameters
        ----------
        request : web.Request
            AIOHTTP request object.
        """

        if request.method == 'GET':
            if request.path == '/match':
                self.logger.debug(f'{request.remote} accessed {self.IP}:{self.port}/match')
                return web.FileResponse('./match_config.json')
            elif request.path == '/map-veto':
                self.logger.debug(f'{request.remote} accessed {self.IP}:{self.port}/map-veto')
                self.map_veto_image_path = self.create_new_veto_filepath()
                response = {'path': self.map_veto_image_path}
                return web.json_response(response)
            elif request.path == self.map_veto_image_path:
                self.logger.debug(f'{request.remote} accessed {self.IP}:{self.port}/{self.map_veto_image_path}')
                return web.FileResponse('./result.png')
            else:
                self.logger.debug(f'{request.remote} accessed {self.IP}:{self.port}{request.path}')
                if os.path.isfile(f'./{request.path}.json'):
                    self.logger.info('File Found')
                    return web.FileResponse(f'./{request.path}.json')
                else:
                    self.logger.error('Invalid Request, File not Found')
                    return WebServer._http_error_handler('file not found')

        # or "Authorization"
        elif request.method == 'POST':
            try:
                get5_event = await request.json()
            except JSONDecodeError:
                self.logger.warning(f'{request.remote} sent a invalid json POST ')
                return WebServer._http_error_handler('json-body')

            # TODO: Create Checks for the JSON

            server = None
            for csgo_server in self.csgo_servers:
                if socket.gethostbyname(csgo_server.server_address) == request.remote:
                    server = csgo_server
                    break

            if server is not None:
                self.logger.debug(f'ServerID={server.id} ({request.remote})=\n {pprint.pformat(get5_event)}')
                if get5_event['event'] == 'knife_start':
                    score_embed = discord.Embed()
                    score_embed.add_field(name=f'0',
                                          value=f'{server.team_names[0]}', inline=True)
                    score_embed.add_field(name=f'0',
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
                    await server.score_message.edit(embed=score_embed)

                elif get5_event['event'] == 'round_end':
                    server.update_team_scores(
                        [get5_event["params"]["team1_score"], get5_event["params"]["team2_score"]])
                    score_embed = discord.Embed()
                    score_embed.add_field(name=f'{get5_event["params"]["team1_score"]}',
                                          value=f'{server.team_names[0]}', inline=True)
                    score_embed.add_field(name=f'{get5_event["params"]["team2_score"]}',
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
                    await server.score_message.edit(embed=score_embed)

                if get5_event['event'] == 'series_end' or get5_event['event'] == 'series_cancel' or get5_event['event'] == 'map_end':
                    if get5_event['event'] == 'series_end':
                        await server.score_message.edit(content='Game Over')
                    elif get5_event['event'] == 'series_cancel':
                        self.logger.info(f'ServerID={server.id} | Admin Cancelled Match')
                        await server.score_message.edit(content='Game Cancelled by Admin')

                    score_embed: discord.Embed = server.score_message.embeds[0]
                    score_embed.set_footer(text='🟥 Ended')
                    await server.score_message.edit(embed=score_embed)

                    if os.path.exists(f'./{get5_event["matchid"]}.json'):
                        os.remove(f'./{get5_event["matchid"]}.json')
                        self.logger.debug(f'Deleted {get5_event["matchid"]}.json')
                    else:
                        self.logger.error(f'Could not delete {get5_event["matchid"]}.json, file does not exist')

                    if self.bot.cogs['CSGO'].pug.enabled:
                        for player in server.players:
                            try:
                                await player.move_to(channel=server.channels[0], reason=f'Game Over')
                            except discord.HTTPException:
                                traceback.print_exc()
                                print(f'Unable to move {player}')
                    await server.channels[1].delete(reason='Game Over')
                    await server.channels[2].delete(reason='Game Over')
                    server.make_available()
                    self.csgo_servers.remove(server)
        else:
            # Used to decline any requests what doesn't match what our
            # API expects.
            self.logger.warning(f'{request.remote} sent an invalid request.')
            return WebServer._http_error_handler("request-type")

        return WebServer._http_error_handler()

    async def http_start(self) -> None:
        """
        Used to start the webserver inside the same context as the bot.
        """
        server = web.Server(self._handler)
        runner = web.ServerRunner(server)
        await runner.setup()
        self.site = web.TCPSite(runner, self.IP, self.port)
        await self.site.start()
        self.logger.info(f'Webserver Started on {self.IP}:{self.port}')

    async def http_stop(self) -> None:
        """
        Used to stop the webserver inside the same context as the bot.
        """
        self.logger.warning(f'Webserver Stopping on {self.IP}:{self.port}')
        await self.site.stop()

    def add_server(self, csgo_server: CSGOServer):
        self.csgo_servers.append(csgo_server)

    @staticmethod
    def _http_error_handler(error: str = 'Undefined Error') -> web.Response:
        """
        Used to handle HTTP error response.
        Parameters
        ----------
        error : bool, optional
            Bool or string to be used, by default False
        Returns
        -------
        web.Response
            AIOHTTP web server response.
        """

        return web.json_response(
            {"error": error},
            status=400 if error else 200
        )
