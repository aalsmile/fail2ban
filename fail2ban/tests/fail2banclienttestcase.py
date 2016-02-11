# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: t -*-
# vi: set ft=python sts=4 ts=4 sw=4 noet :

# This file is part of Fail2Ban.
#
# Fail2Ban is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Fail2Ban is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Fail2Ban; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

# Fail2Ban developers

__author__ = "Serg Brester"
__copyright__ = "Copyright (c) 2014- Serg G. Brester (sebres), 2008- Fail2Ban Contributors"
__license__ = "GPL"

import fileinput
import os
import re
import time
import unittest

from threading import Thread

from ..client import fail2banclient, fail2banserver, fail2bancmdline
from ..client.fail2banclient import Fail2banClient, exec_command_line as _exec_client, VisualWait
from ..client.fail2banserver import Fail2banServer, exec_command_line as _exec_server
from .. import protocol
from ..server import server
from ..server.utils import Utils
from .utils import LogCaptureTestCase, logSys, withtmpdir, shutil, logging


STOCK_CONF_DIR = "config"
STOCK = os.path.exists(os.path.join(STOCK_CONF_DIR,'fail2ban.conf'))
TEST_CONF_DIR = os.path.join(os.path.dirname(__file__), "config")
if STOCK:
	CONF_DIR = STOCK_CONF_DIR
else:
	CONF_DIR = TEST_CONF_DIR

CLIENT = "fail2ban-client"
SERVER = "fail2ban-server"
BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bin")

MAX_WAITTIME = 10
MAX_WAITTIME = unittest.F2B.maxWaitTime(MAX_WAITTIME)

##
# Several wrappers and settings for proper testing:
#

fail2banclient.MAX_WAITTIME = \
fail2banserver.MAX_WAITTIME = MAX_WAITTIME


fail2bancmdline.logSys = \
fail2banclient.logSys = \
fail2banserver.logSys = logSys

LOG_LEVEL = logSys.level

server.DEF_LOGTARGET = "/dev/null"

def _test_output(*args):
	logSys.info(args[0])
fail2bancmdline.output = \
fail2banclient.output = \
fail2banserver.output = \
protocol.output = _test_output

def _test_exit(code=0):
	logSys.debug("Exit with code %s", code)
	if code == 0:
		raise ExitException()
	else:
		raise FailExitException()   
fail2bancmdline.exit = \
fail2banclient.exit = \
fail2banserver.exit = _test_exit

INTERACT = []
def _test_raw_input(*args):
	if len(INTERACT):
		#print('--- interact command: ', INTERACT[0])
		return INTERACT.pop(0)
	else:
		return "exit" 
fail2banclient.raw_input = _test_raw_input

# prevents change logging params, log capturing, etc:
fail2bancmdline.PRODUCTION = False


class ExitException(fail2bancmdline.ExitException):
	pass
class FailExitException(fail2bancmdline.ExitException):
	pass


def _out_file(fn): # pragma: no cover
	logSys.debug('---- ' + fn + ' ----')
	for line in fileinput.input(fn):
		line = line.rstrip('\n')
		logSys.debug(line)
	logSys.debug('-'*30)

def _start_params(tmp, use_stock=False, logtarget="/dev/null"):
	cfg = tmp+"/config"
	if use_stock and STOCK:
		# copy config:
		def ig_dirs(dir, files):
			return [f for f in files if not os.path.isfile(os.path.join(dir, f))]
		shutil.copytree(STOCK_CONF_DIR, cfg, ignore=ig_dirs)
		os.symlink(STOCK_CONF_DIR+"/action.d", cfg+"/action.d")
		os.symlink(STOCK_CONF_DIR+"/filter.d", cfg+"/filter.d")
		# replace fail2ban params (database with memory):
		r = re.compile(r'^dbfile\s*=')
		for line in fileinput.input(cfg+"/fail2ban.conf", inplace=True):
			line = line.rstrip('\n')
			if r.match(line):
				line = "dbfile = :memory:"
			print(line)
		# replace jail params (polling as backend to be fast in initialize):
		r = re.compile(r'^backend\s*=')
		for line in fileinput.input(cfg+"/jail.conf", inplace=True):
			line = line.rstrip('\n')
			if r.match(line):
				line = "backend = polling"
			print(line)
	else:
		# just empty config directory without anything (only fail2ban.conf/jail.conf):
		os.mkdir(cfg)
		f = open(cfg+"/fail2ban.conf", "wb")
		f.write('\n'.join((
			"[Definition]",
			"loglevel = INFO",
			"logtarget = " + logtarget,
			"syslogsocket = auto",
			"socket = "+tmp+"/f2b.sock",
			"pidfile = "+tmp+"/f2b.pid",
			"backend = polling",
			"dbfile = :memory:",
			"dbpurgeage = 1d",
			"",
		)))
		f.close()
		f = open(cfg+"/jail.conf", "wb")
		f.write('\n'.join((
			"[INCLUDES]", "",
			"[DEFAULT]", "",
			"",
		)))
		f.close()
		if LOG_LEVEL < logging.DEBUG: # if HEAVYDEBUG
			_out_file(cfg+"/fail2ban.conf")
			_out_file(cfg+"/jail.conf")
	# parameters:
	return ("-c", cfg, 
					"--logtarget", logtarget, "--loglevel", "DEBUG", "--syslogsocket", "auto",
					"-s", tmp+"/f2b.sock", "-p", tmp+"/f2b.pid")


class Fail2banClientTest(LogCaptureTestCase):

	def setUp(self):
		"""Call before every test case."""
		LogCaptureTestCase.setUp(self)

	def tearDown(self):
		"""Call after every test case."""
		LogCaptureTestCase.tearDown(self)

	def testClientUsage(self):
		self.assertRaises(ExitException, _exec_client, 
			(CLIENT, "-h",))
		self.assertLogged("Usage: " + CLIENT)
		self.assertLogged("Report bugs to ")

	@withtmpdir
	def testClientStartBackgroundInside(self, tmp):
		# always add "--async" by start inside, should don't fork by async (not replace client with server, just start in new process)
		# (we can't fork the test cases process):
		startparams = _start_params(tmp, True)
		# start:
		self.assertRaises(ExitException, _exec_client, 
			(CLIENT, "--async", "-b") + startparams + ("start",))
		self.assertLogged("Server ready")
		self.assertLogged("Exit with code 0")
		try:
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("echo", "TEST-ECHO",))
			self.assertRaises(FailExitException, _exec_client, 
				(CLIENT,) + startparams + ("~~unknown~cmd~failed~~",))
		finally:
			self.pruneLog()
			# stop:
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("stop",))
			self.assertLogged("Shutdown successful")
			self.assertLogged("Exit with code 0")

	@withtmpdir
	def testClientStartBackgroundCall(self, tmp):
		global INTERACT
		startparams = _start_params(tmp)
		# start (without async in new process):
		cmd = os.path.join(os.path.join(BIN), CLIENT)
		logSys.debug('Start %s ...', cmd)
		Utils.executeCmd((cmd,) + startparams + ("start",), 
			timeout=MAX_WAITTIME, shell=False, output=False)
		self.pruneLog()
		try:
			# echo from client (inside):
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("echo", "TEST-ECHO",))
			self.assertLogged("TEST-ECHO")
			self.assertLogged("Exit with code 0")
			self.pruneLog()
			# interactive client chat with started server:
			INTERACT += [
				"echo INTERACT-ECHO",
				"status",
				"exit"
			]
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("-i",))
			self.assertLogged("INTERACT-ECHO")
			self.assertLogged("Status", "Number of jail:")
			self.assertLogged("Exit with code 0")
			self.pruneLog()
			# test reload and restart over interactive client:
			INTERACT += [
				"reload",
				"restart",
				"exit"
			]
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("-i",))
			self.assertLogged("Reading config files:")
			self.assertLogged("Shutdown successful")
			self.assertLogged("Server ready")
			self.assertLogged("Exit with code 0")
			self.pruneLog()
		finally:
			self.pruneLog()
			# stop:
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("stop",))
			self.assertLogged("Shutdown successful")
			self.assertLogged("Exit with code 0")

	def _testClientStartForeground(self, tmp, startparams, phase):
		# start and wait to end (foreground):
		phase['start'] = True
		self.assertRaises(ExitException, _exec_client, 
			(CLIENT, "-f") + startparams + ("start",))
		# end :
		phase['end'] = True

	@withtmpdir
	def testClientStartForeground(self, tmp):
		# started directly here, so prevent overwrite test cases logger with "INHERITED"
		startparams = _start_params(tmp, logtarget="INHERITED")
		# because foreground block execution - start it in thread:
		phase = dict()
		Thread(name="_TestCaseWorker", 
			target=Fail2banClientTest._testClientStartForeground, args=(self, tmp, startparams, phase)).start()
		try:
			# wait for start thread:
			Utils.wait_for(lambda: phase.get('start', None) is not None, MAX_WAITTIME)
			self.assertTrue(phase.get('start', None))
			# wait for server (socket):
			Utils.wait_for(lambda: os.path.exists(tmp+"/f2b.sock"), MAX_WAITTIME)
			self.assertLogged("Starting communication")
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("ping",))
			self.assertRaises(FailExitException, _exec_client, 
				(CLIENT,) + startparams + ("~~unknown~cmd~failed~~",))
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("echo", "TEST-ECHO",))
		finally:
			self.pruneLog()
			# stop:
			self.assertRaises(ExitException, _exec_client, 
				(CLIENT,) + startparams + ("stop",))
			# wait for end:
			Utils.wait_for(lambda: phase.get('end', None) is not None, MAX_WAITTIME)
			self.assertTrue(phase.get('end', None))
			self.assertLogged("Shutdown successful", "Exiting Fail2ban")
			self.assertLogged("Exit with code 0")

	@withtmpdir
	def testClientFailStart(self, tmp):
		self.assertRaises(FailExitException, _exec_client, 
			(CLIENT, "--async", "-c", tmp+"/miss", "start",))
		self.assertLogged("Base configuration directory " + tmp+"/miss" + " does not exist")

		self.assertRaises(FailExitException, _exec_client, 
			(CLIENT, "--async", "-c", CONF_DIR, "-s", tmp+"/miss/f2b.sock", "start",))
		self.assertLogged("There is no directory " + tmp+"/miss" + " to contain the socket file")

	def testVisualWait(self):
		sleeptime = 0.035
		for verbose in (2, 0):
			cntr = 15
			with VisualWait(verbose, 5) as vis:
				while cntr:
					vis.heartbeat()
					if verbose and not unittest.F2B.fast:
						time.sleep(sleeptime)
					cntr -= 1


class Fail2banServerTest(LogCaptureTestCase):

	def setUp(self):
		"""Call before every test case."""
		LogCaptureTestCase.setUp(self)

	def tearDown(self):
		"""Call after every test case."""
		LogCaptureTestCase.tearDown(self)

	def testServerUsage(self):
		self.assertRaises(ExitException, _exec_server, 
			(SERVER, "-h",))
		self.assertLogged("Usage: " + SERVER)
		self.assertLogged("Report bugs to ")

	@withtmpdir
	def testServerStartBackground(self, tmp):
		# don't add "--async" by start, because if will fork current process by daemonize
		# (we can't fork the test cases process),
		# because server started internal communication in new thread use INHERITED as logtarget here:
		startparams = _start_params(tmp, logtarget="INHERITED")
		# start:
		self.assertRaises(ExitException, _exec_server, 
			(SERVER, "-b") + startparams)
		self.assertLogged("Server ready")
		self.assertLogged("Exit with code 0")
		try:
			self.assertRaises(ExitException, _exec_server, 
				(SERVER,) + startparams + ("echo", "TEST-ECHO",))
			self.assertRaises(FailExitException, _exec_server, 
				(SERVER,) + startparams + ("~~unknown~cmd~failed~~",))
		finally:
			self.pruneLog()
			# stop:
			self.assertRaises(ExitException, _exec_server, 
				(SERVER,) + startparams + ("stop",))
			self.assertLogged("Shutdown successful")
			self.assertLogged("Exit with code 0")

	def _testServerStartForeground(self, tmp, startparams, phase):
		# start and wait to end (foreground):
		phase['start'] = True
		self.assertRaises(ExitException, _exec_server, 
			(SERVER, "-f") + startparams + ("start",))
		# end :
		phase['end'] = True
	@withtmpdir
	def testServerStartForeground(self, tmp):
		# started directly here, so prevent overwrite test cases logger with "INHERITED"
		startparams = _start_params(tmp, logtarget="INHERITED")
		# because foreground block execution - start it in thread:
		phase = dict()
		Thread(name="_TestCaseWorker", 
			target=Fail2banServerTest._testServerStartForeground, args=(self, tmp, startparams, phase)).start()
		try:
			# wait for start thread:
			Utils.wait_for(lambda: phase.get('start', None) is not None, MAX_WAITTIME)
			self.assertTrue(phase.get('start', None))
			# wait for server (socket):
			Utils.wait_for(lambda: os.path.exists(tmp+"/f2b.sock"), MAX_WAITTIME)
			self.assertLogged("Starting communication")
			self.assertRaises(ExitException, _exec_server, 
				(SERVER,) + startparams + ("ping",))
			self.assertRaises(FailExitException, _exec_server, 
				(SERVER,) + startparams + ("~~unknown~cmd~failed~~",))
			self.assertRaises(ExitException, _exec_server, 
				(SERVER,) + startparams + ("echo", "TEST-ECHO",))
		finally:
			self.pruneLog()
			# stop:
			self.assertRaises(ExitException, _exec_server, 
				(SERVER,) + startparams + ("stop",))
			# wait for end:
			Utils.wait_for(lambda: phase.get('end', None) is not None, MAX_WAITTIME)
			self.assertTrue(phase.get('end', None))
			self.assertLogged("Shutdown successful", "Exiting Fail2ban")
			self.assertLogged("Exit with code 0")

	@withtmpdir
	def testServerFailStart(self, tmp):
		self.assertRaises(FailExitException, _exec_server, 
			(SERVER, "-c", tmp+"/miss",))
		self.assertLogged("Base configuration directory " + tmp+"/miss" + " does not exist")

		self.assertRaises(FailExitException, _exec_server, 
			(SERVER, "-c", CONF_DIR, "-s", tmp+"/miss/f2b.sock",))
		self.assertLogged("There is no directory " + tmp+"/miss" + " to contain the socket file")
