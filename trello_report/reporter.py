#!/usr/bin/env python2

# Copyright (c) 2016 Ryan Brady <ryan@ryanbrady.org>
# Copyright (c) 2016 Ihar Hrachyshka <ihrachys@redhat.com>

# This tool will produce a nice email friendly report from your Trello
# 'Personal:Done' list contents, and archive all the cards from the list to
# give space for a next report.

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

import datetime
import dateutil.parser
import os
from rstcloth import rstcloth
import sys

import click
from trello import trelloclient


SKIP_LABEL = 'Skip-for-report'


class CardData(object):
    def __init__(self, card, updates_since=None):
        super(CardData, self).__init__()
        self._card = card
        # cache data to avoid later refetches
        self._comments = [
            c for c in card.get_comments()
            if (updates_since is None
                or dateutil.parser.isoparse(c['date']) > updates_since)]
        self._attachments = card.get_attachments()
        self._checklists = card.fetch_checklists()

    def __hash__(self):
        return self._card.__hash__()

    @property
    def name(self):
        return self._card.name

    @property
    def description(self):
        desc = self._card.desc
        return desc if desc else None

    @property
    def labels(self):
        if not self._card.labels:
            return []
        return [l.name for l in self._card.labels]

    @property
    def comments(self):
        l = []
        for comment in self._comments:
            if comment['type'] == 'commentCard':
                if 'data' in comment and 'text' in comment['data']:
                    l.append(comment['data']['text'])
        return l

    @property
    def attachments(self):
        l = []
        for attachment in self._attachments:
            l.append(attachment.url)
        return l

    @property
    def checklists(self):
        l = []
        for checklist in self._checklists:
            d = {'name': checklist.name, 'items': []}
            for n in checklist.items:
                d['items'].append(
                    "%s %s" % ('[X]' if n['checked'] else '[ ]', n['name']))
            l.append(d)
        return l

    @property
    def date_last_activity(self):
        return self._card.date_last_activity

    def updated_since(self, timestamp):
        return timestamp is None or self.date_last_activity > timestamp

    def __str__(self):
        d = rstcloth.RstCloth()
        d.h3(self.name)
        d.newline()
        if self.description:
            d.content(self.description)
            d.newline()
        comments = list(self.comments)

        if comments:
            for comment in comments:
                d.li([comment])  # work around rstcloth bug
            d.newline()

        for checklist in self.checklists:
            d.h4(checklist['name'] or 'Checklist')
            for element in checklist['items']:
                d.newline()
                d.li([element])  # work around rstcloth bug
            d.newline()

        for attachment in self.attachments:
            d.footnote('', attachment)

        return "\n".join(d.data)


def get_board(api, name):
    api.list_boards()
    board = [b for b in api.list_boards() if b.name == name]
    return board[0] if board else None


def get_board_labels(board, top_labels, bottom_labels):
    labels = {l.name for l in board.get_labels()
              if l.name != SKIP_LABEL}

    # arrange the labels in a more semantically correct order
    res = []

    # first, put high visible topics at the top
    for label in top_labels:
        if label in labels:
            res.append(label)
            labels.remove(label)

    # include uncategorized topics except those that go at the very end
    for label in set(labels):
        if label not in bottom_labels:
            res.append(label)
            labels.remove(label)

    # finally, include those topics going to the very bottom
    res += list(labels)

    return res


def get_list(board, name):
    list_ = [l for l in board.open_lists() if l.name == name]
    return list_[0] if list_ else None


def get_cards(l, updated_since=None):
    updated_cards = set()
    stale_cards = set()
    for card in l.list_cards():
        card = CardData(card, updates_since=updated_since)
        if SKIP_LABEL in card.labels:
            continue
        if card.updated_since(updated_since):
            updated_cards.add(card)
        else:
            stale_cards.add(card)
    return updated_cards, stale_cards


def get_cards_by_label(cards, label):
    return {card for card in cards if label in card.labels}


# copy-pasted from https://github.com/rbrady/filch/blob/master/filch/helpers.py
def get_config_info():
    config_path = os.environ.get('TRELLO_REPORTER_CONFIG',
                                 os.path.expanduser('~/.trello_reporter.conf'))
    config = configparser.SafeConfigParser()
    if not config.read(config_path):
        click.echo('Failed to parse config file {}.'.format(config_path))
        sys.exit(1)
    if not config.has_section('trello'):
        click.echo('Config file does not contain section [trello].')
        sys.exit(1)
    trello_data = dict(config.items('trello'))
    required_settings = ['api_key', 'access_token']
    for setting in required_settings:
        if setting not in trello_data:
            click.echo(
                'Config file requires a setting for {} '
                'in section [trello].'.format(setting)
            )
            sys.exit(1)
    return trello_data


@click.command()
@click.option('--days', type=int, default=None,
              help="Cards not updated in n days go in their own section")
@click.option('--skip-not-updated', default=False, is_flag=True,
              help="Skip cards that haven't been updated in --days")
def main(days, skip_not_updated):
    config_info = get_config_info()
    api = trelloclient.TrelloClient(api_key=config_info['api_key'],
                                    token=config_info['access_token'])

    b = get_board(api, config_info['board'])
    assert b is not None

    in_progress_list = get_list(b, config_info['in_progress_list'])
    assert in_progress_list is not None

    done_list = get_list(b, config_info['done_list'])
    assert done_list is not None

    top_labels = [l.strip() for l in config_info['top_labels'].split(',')]
    end_labels = [l.strip() for l in config_info['bottom_labels'].split(',')]

    print('Report generated on: %s.' % datetime.date.today().isoformat())
    print('Generated by: https://github.com/booxter/trello-report\n')

    labels = get_board_labels(b, top_labels, end_labels)
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)) if days else None
    d = rstcloth.RstCloth()
    for l in (in_progress_list, done_list):
        d.h1(l.name)
        d.newline()
        cards, not_updated = get_cards(l, updated_since=since)
        for label in labels:
            labeled_cards = get_cards_by_label(cards, label)
            if labeled_cards:
                d.h2(label)
                for card in labeled_cards:
                    d.newline()
                    d._add(str(card))
                    cards.remove(card)
                d.newline()

        # handle remaining, unlabeled cards
        if cards:
            d.h2('Other')
            for card in cards:
                d.newline()
                d._add(str(card))
            d.newline()
        # special section for cards that haven't had updates this week
        if not_updated and not skip_not_updated:
            d.h2("Not Updated")
            for card in not_updated:
                d.newline()
                d._add(str(card))

    d.print_content()

    # finally, archive all cards that we just reported on
    # done_list.archive_all_cards()


if __name__ == '__main__':
    main()
