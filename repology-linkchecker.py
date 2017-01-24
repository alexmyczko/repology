#!/usr/bin/env python3
#
# Copyright (C) 2016 Dmitry Marakasov <amdmi3@amdmi3.ru>
#
# This file is part of repology
#
# repology is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# repology is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with repology.  If not, see <http://www.gnu.org/licenses/>.

import os
import re
import argparse
import requests
import urllib.parse
import time

from repology.database import Database
from repology.logger import StderrLogger, FileLogger

import repology.config


def GetHTTPLinkStatus(url):
    try:
        response = requests.head(url, allow_redirects=True, headers={'user-agent': "Repology link checker/0"})

        redirect = None
        size = None
        location = None

        # handle redirect chain
        if response.history:
            redirect = response.history[0].status_code

            # resolve permanent (and only permament!) redirect chain
            for h in response.history:
                if h.status_code == 301:
                    location = h.headers.get('location')

        # handle size
        if response.status_code == 200:
            content_length = response.headers.get('content-length')
            if content_length:
                size = int(content_length)

        return (url, response.status_code, redirect, size, location)
    except KeyboardInterrupt:
        raise
    except requests.Timeout:
        return (url, Database.linkcheck_status_timeout, None, None, None)
    except requests.TooManyRedirects:
        return (url, Database.linkcheck_status_too_many_redirects, None, None, None)
    except requests.ConnectionError:
        return (url, Database.linkcheck_status_cannot_connect, None, None, None)
    except requests.exceptions.InvalidURL:
        return (url, Database.linkcheck_status_invalid_url, None, None, None)
    except:
        raise
        return (url, Database.linkcheck_status_unknown_error, None, None, None)


def ProcessLinksPack(pack, options, logger):
    database = Database(options.dsn, readonly=False)

    results = []
    prev_host = None
    for url in pack:
        # XXX: add support for gentoo mirrors, skip for now
        if not url.startswith("http://") and not url.startswith("https://"):
            logger.Log("  Skipping {}, unsupported schema".format(url))
            continue

        logger.Log("  Processing {}".format(url))

        host = urllib.parse.urlparse(url).hostname

        if host and host == prev_host:
            time.sleep(options.delay)

        results.append(GetHTTPLinkStatus(url))

        prev_host = host

    logger.Log("Writing pack")

    for result in results:
        url, status, redirect, size, location = result
        database.UpdateLinkStatus(url=url, status=status, redirect=redirect, size=size, location=location)

    logger.Log("  Committing")

    database.Commit()

    logger.Log("    Done")


def Main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-D', '--dsn', default=repology.config.DSN, help='database connection params')
    parser.add_argument('-L', '--logfile', help='path to log file (log to stderr by default)')

    parser.add_argument('-t', '--timeout', type=int, default=60, help='timeout for link requests in seconds')
    parser.add_argument('-d', '--delay', type=float, default=3.0, help='delay between requests to a single host')
    parser.add_argument('-a', '--age', type=int, default=365, help='min age for recheck in days')
    parser.add_argument('-p', '--packsize', type=int, default=128, help='pack size for link processing')
    parser.add_argument('-j', '--jobs', type=int, default=128, help='pack size for link processing')
    options = parser.parse_args()

    logger = StderrLogger()
    if options.logfile:
        logger = FileLogger(options.logfile)

    database = Database(options.dsn, readonly=True)

    prev_url = None
    while True:
        # Get pack of links
        logger.Log("Requesting pack of urls".format(prev_url))
        urls = database.GetLinksForCheck(after=prev_url, limit=options.packsize, recheck_age=options.age * 60 * 60 * 24)
        if not urls:
            logger.Log("  Empty pack, we're done")
            break

        logger.Log("  {} urls(s)".format(len(urls)))

        # Get another pack of urls with the last hostname to ensure
        # that all urls for one hostname get into a same large pack
        match = re.match('([a-z]+://[^/]+/)', urls[-1])
        if match:
            logger.Log("Requesting additinonal pack of urls with common prefix")
            urls += database.GetLinksForCheck(after=urls[-1], prefix=match.group(1), recheck_age=options.age * 60 * 60 * 24)

        logger.Log("  {} total urls(s)".format(len(urls)))

        # Process
        logger.Log("Processing pack of {} urls ({}:{})".format(len(urls), urls[0], urls[-1]))

        ProcessLinksPack(urls, options, logger)

        logger.Log("  Done")

        prev_url = urls[-1]

    return 0


if __name__ == '__main__':
    os.sys.exit(Main())
