import os

from hvac import Client
from hvac.api.auth_methods.approle import AppRole
from hvac.adapters import JSONAdapter

from wowahf.cfg.Config import Config
from wowahf.utils.logger import get_logger

logger = get_logger()


class VaultConfig(Config):
    def __init__(self, config_path):
        super().__init__(config_path)
        logger.info("Getting secrets...")
        self.config_path = config_path
        self.config = {}
        self.secret_id = os.environ.get('VAULT_SECRET_ID') or ""
        self.role_id = os.environ.get('VAULT_ROLE_ID') or ""
        self.vault_addr = os.environ.get('VAULT_ADDR') or ""
        self.vault_port = os.environ.get('VAULT_PORT') or ""
        self.pre_start_check()
        self.login()
        logger.info("Done getting secrets.")
        self.valid = True
        logger.debug("{}: Loaded config with {} entries.".format(self.__class__.__name__, len(self.config)))

    def login(self):
        try:
            if not self.secret_id or not self.role_id or not self.vault_port or not self.vault_port:
                raise RuntimeError("Cannot log in to vault: Insufficient config data.")
            url = "{}:{}".format(self.vault_addr, self.vault_port)
            adapter = JSONAdapter(base_uri=url)
            approle = AppRole(adapter=adapter)
            resp = approle.login(self.role_id, secret_id=self.secret_id)
            client = Client(url=url)
            if resp and resp['auth'] and resp['auth']['client_token']:
                client.token = resp['auth']['client_token']
                self.config = client.read(self.config_path)
                if self.config and self.config is not None and "data" in self.config:
                    self.config = self.config["data"]["data"]
                    return True
                else:
                    logger.error("Authenticated, but there is no config under {}".format(self.config_path))
                    exit(1)
            else:
                logger.error("Could not authenticate in vault using the provided credentials.")
                exit(1)
        except Exception as e:
            logger.error("Could not login to vault: {}".format(e.__class__.__name__))
            exit(1)

    def pre_start_check(self):
        logger.info("Checking start requirements...")
        ok = True
        if not self.secret_id:
            logger.warning("Secret ID is not present")
            ok = False
        if not self.role_id:
            logger.warning("Role ID is not present")
            ok = False
        if not self.vault_addr:
            logger.warning("Vault address is not present")
            ok = False
        if not self.vault_port:
            logger.warning("Vault port is not present")
            ok = False
        if not ok:
            logger.error("Start requirements are not satisfied. Cannot start.")
            exit(1)
        else:
            logger.info("Start requirements satisfied. Moving on...")
