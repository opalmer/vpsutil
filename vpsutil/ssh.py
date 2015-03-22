import os
import shutil
import socket
import subprocess
import time
from collections import namedtuple
from errno import EEXIST, ENOENT
from os.path import join, isdir, isfile

try:
    ConnectionRefusedError
except NameError:
    ConnectionRefusedError = OSError

import paramiko
from vpsutil.logger import logger
from vpsutil.config import CONFIG_FILE, CONFIG_DIR_SSH, config

RSAKeyPair = namedtuple("RSAKeyPair", ("public", "private"))


class SSHClient(object):
    def __init__(self, user, host, key_pair_dir, retry_connect=True):
        key_pair = None
        if isdir(key_pair_dir):
            key_pair = self.get_key_pair(key_pair_dir)

        assert isinstance(key_pair, RSAKeyPair)
        self.user = user
        self.host = host
        self.key_pair = key_pair
        self.ssh = self.connect(retry_connect=retry_connect)

    @classmethod
    def generate_rsa_key_pair(cls, output_dir=None, name=None, bits=2048):
        """
        Generates a public/private key pair in the given directory
        with the number of requested bits.
        """
        assert output_dir is not None or name is not None
        logger.info("Generating %d-bit RSA key pair in %s", bits, output_dir)

        if output_dir is None:
            output_dir = join(CONFIG_DIR_SSH, name)
            try:
                os.makedirs(output_dir)
            except (OSError, IOError) as e:
                if e.errno != EEXIST:
                    raise

        private_file = join(output_dir, "id_rsa")
        public_file = private_file + ".pub"

        # Generate private key
        private_key = paramiko.RSAKey.generate(bits=2048)
        private_key.write_private_key_file(private_file)

        # Generate public key
        public_key = paramiko.RSAKey(filename=private_file)
        with open(public_file, "w") as public_file_stream:
            public_file_stream.write(
                "%s %s" % (public_key.get_name(), public_key.get_base64()))

        if name is not None:
            if not config.has_section("ssh_keys"):
                config.add_section("ssh_keys")
            config.set("ssh_keys", name, output_dir)

            shutil.copy2(CONFIG_FILE, CONFIG_FILE + ".last")
            with open(CONFIG_FILE, "w") as config_file:
                config.write(config_file)

        return RSAKeyPair(public=public_file, private=private_file)

    @classmethod
    def delete_rsa_key_pair(cls, name):
        """Deletes an RSA key pair from the config and on disk"""
        path = config.get("ssh_keys", name)
        try:
            shutil.rmtree(path)
            logger.warning("Deleted local RSA key pair for %s", name)
        except (OSError, IOError) as e:
            if e.errno != ENOENT:
                raise
        config.remove_option("ssh_keys", name)

        shutil.copy2(CONFIG_FILE, CONFIG_FILE + ".last")
        with open(CONFIG_FILE, "w") as config_file:
            config.write(config_file)

    @classmethod
    def get_key_pair(cls, directory):
        assert isdir(directory)
        private_key = join(directory, "id_rsa")
        public_key = private_key + ".pub"
        assert isfile(public_key)
        assert isfile(private_key)
        return RSAKeyPair(public=public_key, private=private_key)

    def connect(self, retry_connect=True):
        logger.info("Attempting SSH connection via %s@%s", self.user, self.host)

        # First, try to ping the first.  This is more reliable usually
        # than just constantly trying to connect.
        start = time.time()
        ping_count = 0
        while True:
            try:
                subprocess.check_output(["ping", "-c", "1", self.host])
                break
            except subprocess.CalledProcessError:
                pass

            logger.debug("... ping failed")
            ping_count += 1
            time.sleep(30)

        logger.debug("... ping complete in %s seconds", time.time() - start)

        start = time.time()
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        logger.debug("... attempting ssh connection")
        while True:
            try:
                ssh.connect(
                    username=self.user, hostname=self.host,
                    key_filename=self.key_pair.private,
                    timeout=5, banner_timeout=3
                )
                break
            except (socket.timeout, ConnectionRefusedError) as error:
                if not retry_connect:
                    raise

                logger.debug("... ssh connect() failed: %s", error)
            time.sleep(10)

        logger.debug(
            "... ssh connection complete in %s seconds", time.time() - start)

        return ssh