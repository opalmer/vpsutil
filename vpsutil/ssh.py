import atexit
import os
import shutil
import socket
import subprocess
import time
from collections import namedtuple
from configparser import NoOptionError, NoSectionError
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
CommandResult = namedtuple("CommandResult", ("stdout", "stderr"))


class SSHClient(object):
    """
    An SSH client to connect to connect and communicate with a remote host.

    >>> with SSHClient("root", REMOTE_IP, NAME, wait_for_connect=True) as ssh:
    ...    ssh.run("apt-get update")
    ...    ssh.run("apt-get -y dist-upgrade")
    ...    ssh.run("apt-get -y autoremove")
    ...    # more commands to setup the host
    """
    def __init__(self, user, host, key_pair, wait_for_connect=True):
        if isinstance(key_pair, str) and not isdir(key_pair):
            key_pair = self.get_key_pair(key_pair)

        assert isinstance(key_pair, RSAKeyPair)
        self.user = user
        self.host = host
        self.key_pair = key_pair
        self.wait_for_connect = wait_for_connect
        self._client = None
        self._sftp = None
        atexit.register(self.close)

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
        try:
            path = config.get("ssh_keys", name)
        except (NoOptionError, NoSectionError):
            return
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
    def get_key_pair(cls, name):
        # First try to get the key pair by name
        try:
            name = config.get("ssh_keys", name)
        except (NoOptionError, NoSectionError):
            pass

        private_key = join(name, "id_rsa")
        public_key = private_key + ".pub"
        assert isfile(public_key), "not a file %s" % public_key
        assert isfile(private_key), "not a file %s" % private_key
        return RSAKeyPair(public=public_key, private=private_key)

    @property
    def client(self):
        if self._client is not None:
            return self._client

        self._client = self.connect(wait_for_connect=self.wait_for_connect)
        return self._client

    @property
    def sftp(self):
        if self._sftp is not None:
            return self._sftp

        self._sftp = self.client.open_sftp()
        return self._sftp

    def __enter__(self):
        self._client = self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self, wait_for_connect=True):
        logger.info("Attempting SSH connection via %s@%s", self.user, self.host)

        if wait_for_connect:
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
                if not wait_for_connect:
                    raise

                logger.debug("... ssh connect() failed: %s", error)
            time.sleep(10)

        logger.debug(
            "... ssh connection complete in %s seconds", time.time() - start)

        return ssh

    def close(self):
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
            logger.debug("Closed SFTP connection")

        if self._client is not None:
            self._client.close()
            self._client = None
            logger.debug("Closed SSH connection")

    def run(self, command, echo=True, read_output=True):
        if echo:
            logger.debug("executing: %s", command)
        else:
            logger.debug("executing: %s", "*" * len(command))

        start = time.time()
        stdin, stdout, stderr = self.client.exec_command(command)
        status = stderr.channel.recv_exit_status()
        if status != 0:
            logger.error("  exit: %s", status)
            logger.error("stdout: %s", stdout.read())
            logger.error("stderr: %s", stderr.read())
            raise ValueError("Non-zero exit status.")

        if read_output:
            result = CommandResult(
                stdout=stdout.read().decode(),
                stderr=stderr.read().decode())

        else:
            result = CommandResult(stdout=stdout, stderr=stderr)

        elapsed = time.time() - start
        if echo:
            logger.info("executed (%0.2fs): %s", elapsed, command)
        else:
            logger.info("executed (%0.2fs): %s", elapsed, "*" * len(command))

        return result

    def add_iptables_rule(self, rule, check_first=False):
        """
        Adds an iptables rule if appears that the rule does not
        already exist.  We are under the assumption that the input rule
        does not contain "iptables" or the sudo command
        """
        # TODO: Better handling of iptables/sudo would be nice
        iptables_save = self.run("iptables-save")

        message = "add iptables rule: %s"
        should_run = True
        if check_first and rule in iptables_save.stdout:
            should_run = False
            message += " (exists)"

        if should_run:
            self.run("iptables " + rule)

        logger.info(message % rule)
