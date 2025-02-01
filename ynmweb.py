import sopel
import requests
import time
import threading
import os
import logging
import traceback
import sys
from datetime import timedelta

class YnMWebPlugin:
    def __init__(self, bot):
        self.bot = bot
        
        try:
            
            self.api_url = bot.config.ynmweb.api_url
            self.api_key = bot.config.ynmweb.api_key
        except AttributeError:
            logging.error("YnM Web configuration not found.")
            return

        if not self.api_url or not self.api_key:
            logging.error("Invalid YnM Web configuration: URL or API key is missing")
            return
        
        # Configure detailed logging
        logging.basicConfig(
            level=logging.DEBUG, 
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(os.path.join(bot.config.core.homedir, 'ynmweb_debug.log')),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger('sopel.plugins.ynmweb')
        
        # Verzió küldés egyszeri kezdeményezése a bot indításakor
        self.send_version()

        
        # Start background threads
        self.stop_event = threading.Event()
        threading.Thread(target=self.api_loop, daemon=True, name="YnMWebAPILoop").start()
        threading.Thread(target=self.update_channels, daemon=True, name="YnMWebChannelUpdate").start()
        threading.Thread(target=self.update_uptime, daemon=True, name="YnMWebUptimeUpdate").start()
        threading.Thread(target=self.update_server_uptime, daemon=True, name="YnMWebServerUptimeUpdate").start()
         

    def send_version(self):
        """Elküldi a bot verzióját egyszer, indításkor."""
        try:
            sopel_version = sopel.__version__  # Sopel verzió
            python_version = sys.version  # Python verzió

            # Az API számára elküldendő verzió string összeállítása
            version_info = f"Sopel: {sopel_version} | Python: {python_version}"

            # API kérés verzióval
            self._make_api_request('version', version=version_info)
        except Exception as e:
            self.logger.error(f"Version Send Error: {e}")
    
    
    def _make_api_request(self, command, **params):
        full_params = {
            'key': self.api_key,
            'command': command,
            **params
        }
        
        self.logger.debug(f"API Request Details:")
        self.logger.debug(f"URL: {self.api_url}")
        self.logger.debug(f"Command: {command}")
        
        for key, value in full_params.items():
            if key == 'key':
                # Mask API key in logs
                self.logger.debug(f"Param {key}: {'*' * len(str(value))}")
            else:
                self.logger.debug(f"Param {key}: {value}")

        try:
            response = requests.post(
                self.api_url, 
                data=full_params, 
                timeout=10,
                verify=True  # Ensure SSL certificate verification
            )
            
            self.logger.debug(f"Response Status Code: {response.status_code}")
            self.logger.debug(f"Response Headers: {response.headers}")
            self.logger.debug(f"Response Content: {response.text}")

            response.raise_for_status()  # Raise exception for bad HTTP status
            return response.json()

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Network Error: {e}")
            self.logger.error(traceback.format_exc())
        except ValueError as e:
            self.logger.error(f"JSON Parsing Error: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected Error: {e}")
            self.logger.error(traceback.format_exc())
        
        return None

    def api_loop(self):
        while not self.stop_event.is_set():
            try:
                response = self._make_api_request('fetch')
                if response and 'message' in response:
                    self._process_messages(response['message'])
            except Exception as e:
                self.logger.error(f"API Loop Error: {e}")
            time.sleep(180)

    def _process_messages(self, messages):
        for msg in messages:
            command = msg.get('command', '').lower()
            msg_id = msg.get('id')
            
            handlers = {
                'rehash': self._handle_rehash,
                'restart': self._handle_restart,
                'die': self._handle_die,
                'join': self._handle_join,
                'part': self._handle_part
            }
            
            handler = handlers.get(command)
            if handler:
                handler(msg_id, msg.get('arguments', ''))

    def _pickup(self, msg_id, success, msg=''):
        self._make_api_request('pickup', action=msg_id, success=success, message=msg)

    def _handle_join(self, msg_id, channel):
        if channel.startswith('#'):
            try:
                self.bot.join(channel)
                self._pickup(msg_id, 1, f"Add {channel}")
            except Exception as e:
                self._pickup(msg_id, 0, str(e))
        else:
            self._pickup(msg_id, 0, f"Invalid channel name: {channel}")

    def _handle_part(self, msg_id, channel):
        if channel.startswith('#'):
            try:
                self.bot.part(channel)
                self._pickup(msg_id, 1, f"Del {channel}")
            except Exception as e:
                self._pickup(msg_id, 0, str(e))
        else:
            self._pickup(msg_id, 0, f"Invalid channel name: {channel}")

    def _handle_rehash(self, msg_id, _):
        self._pickup(msg_id, 1, "Reloading configuration...")
        setup(self.bot)  # Újrahívja a plugin betöltését

    def _handle_restart(self, msg_id, _):
        self._pickup(msg_id, 1, "Restarting bot...")
        self.bot.restart()

    def _handle_die(self, msg_id, _):
        self._pickup(msg_id, 1, "Shutting down bot...")
        self.stop_event.set()  # Háttérszálak leállítása
        self.bot.quit("Bot shutting down...")  # IRC kapcsolat normálisan bontása
        time.sleep(1)  # Várunk kicsit, hogy befejezze
        sys.exit(0)  # Tiszta kilépés

    def update_channels(self):
        while not self.stop_event.is_set():
            try:
                channels = self.bot.channels
                channel_list = ','.join(channels)
                self._make_api_request('updatechannels', channels=channel_list)
            except Exception as e:
                self.logger.error(f"Channel Update Error: {e}")
            time.sleep(300)

    def update_uptime(self):
        while not self.stop_event.is_set():
            try:
                current_time = int(time.time())
                uptime = current_time - self.bot.startup_time  # Sopel saját uptime-ja
                readable_uptime = str(timedelta(seconds=uptime))
                self._make_api_request('ontime', on_time=readable_uptime)
            except Exception as e:
                self.logger.error(f"Uptime Update Error: {e}")
            time.sleep(90)  

    def update_server_uptime(self):
        while not self.stop_event.is_set():
            try:
                with open('/proc/uptime', 'r') as f:
                    uptime_seconds = float(f.read().split()[0])
                    readable_uptime = str(timedelta(seconds=int(uptime_seconds)))
                    self._make_api_request('uptime', server_uptime=readable_uptime)
            except Exception as e:
                self.logger.error(f"Server Uptime Update Error: {e}")
            time.sleep(90)

def configure(config):
    config.define_section('ynmweb', 'YnM Web Configuration')
    config.ynmweb.configure_setting('api_url', 'Enter the YnM Web API URL')
    config.ynmweb.configure_setting('api_key', 'Enter the YnM Web API Key')

def setup(bot):
    bot.startup_time = int(time.time())  # Indulási idő rögzítése
    bot.ynm_plugin = YnMWebPlugin(bot)

def shutdown(bot):
    if hasattr(bot, 'ynm_plugin'):
        bot.ynm_plugin.stop_event.set()
