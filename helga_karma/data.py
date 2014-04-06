import datetime
import math
import sys

import pymongo
import six

from helga import log, settings
from helga.db import db


logger = log.getLogger(__name__)


class KarmaRecord(object):
    def __init__(self, record):
        self._record = record

    @classmethod
    def get_actual_nick(cls, nick):
        if nick.find('|'):
            nick = nick.split('|')[0]
        record = db.karma_link.find_one(
            {'nick': nick}
        )
        if record:
            return record['real_nick']
        return nick

    @classmethod
    def get_empty_record(self, nick):
        return {
            'nick': nick,
            'given': 0,
            'received': 0,
            'value': 0,
            'created': datetime.datetime.utcnow(),
            'last_received': None,
            'last_given': None,
        }

    @classmethod
    def get_for_nick(cls, nick, get_empty=True, use_aliases=True):
        if use_aliases:
            nick = cls.get_actual_nick(nick)
        record = cls.get_empty_record(nick)

        result = db.karma_user.find_one({'nick': nick})
        if not get_empty and not result:
            return None
        if result:
            record.update(result)
        return cls(record)

    @classmethod
    def get_top(cls, limit=10):
        for result in (
            db.karma_user.find()
            .sort('value', direction=pymongo.DESCENDING)
            .limit(limit)
        ):
            yield cls(result)

    @classmethod
    def get_global_karma_maximum(cls):
        top_1 = list(cls.get_top(limit=1))
        if not top_1:
            return 0
        return top_1[0].get('value', 0)

    def add_alias(self, other):
        for key in ['given', 'received', 'value']:
            self[key] = self[key] + other[key]

        for key in ['last_received', 'last_given']:
            if (other[key] and self[key] and other[key] > self[key]):
                self[key] = other[key]

        self._add_alias_record(other)
        self.save()
        other.delete()

    def remove_alias(self, nick):
        alias = db.karma_link.find_one({'nick': nick})
        db.karma_link.remove({'nick': nick})

        other = KarmaRecord(alias['record'])
        other.save()

        for key in ['given', 'received', 'value']:
            self[key] = self[key] - other[key]

        self.save()
        other.transfer_aliases_from(self, subset=alias['aliases'])

    def transfer_aliases_from(self, record, subset=None):
        for existing_alias in record._get_alias_records():
            if not subset or existing_alias['nick'] in subset:
                existing_alias['real_nick'] = self['nick']
                db.karma_link.update(
                    {'nick': existing_alias['nick']},
                    existing_alias,
                )

    def _add_alias_record(self, record):
        # Update aliases assigned to `record` to point at `self`.
        record_aliases = record.get_aliases()
        self.transfer_aliases_from(record)

        return db.karma_link.insert(
            {
                'nick': record['nick'],
                'real_nick': self['nick'],
                'record': dict(record),
                'aliases': record_aliases,
            }
        )

    def get_aliases(self):
        return [record['nick'] for record in self._get_alias_records()]

    def _get_alias_records(self):
        return db.karma_link.find(
            {'real_nick': self['nick']}
        )

    def give_karma_to(self, other, count=1):
        value = count * self.get_coefficient()

        self['given'] = self['given'] + 1
        self['last_given'] = datetime.datetime.utcnow()
        self.save()

        other['value'] = other['value'] + value
        other['received'] = other['received'] + 1
        other['last_received'] = datetime.datetime.now()
        other.save()

        logger.info(
            "Gave %s karma from %s to %s",
            value,
            self,
            other,
        )

        return value

    def get_value(self):
        output_scale_min, output_scale_max = getattr(
            settings,
            'KARMA_SCALED_RANGE',
            (0, 0),
        )
        if not output_scale_max:
            return self.get('value', 0)

        maximum_karma = float(self.get_global_karma_maximum())
        my_karma = self.get('value', 0)

        if maximum_karma == 0:
            return 0

        if getattr(settings, 'KARMA_SCALE_LINEAR', False):
            # Linearly scale karma
            percentage = my_karma / maximum_karma
        else:
            # Logarithmically scale karma
            percentage = math.log(my_karma + 1, maximum_karma + 1)

        return (
            percentage * (output_scale_max - output_scale_min)
        ) + output_scale_min

    def get_coefficient(self):
        return (
            max(float(self._record['received']), 1.0)
            / max(self._record['given'], 1)
        )

    def save(self):
        db.karma_user.update(
            {'nick': self['nick']},
            self._record,
            upsert=True,
        )

    def delete(self):
        db.karma_user.remove({'nick': self['nick']})

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __getitem__(self, key):
        return self._record[key]

    def __setitem__(self, key, value):
        self._record[key] = value

    def __iter__(self):
        return six.iteritems(self._record)

    def __str__(self):
        if sys.version_info > (3, 0):
            return self.__unicode__()
        return self.__unicode__().encode(sys.getdefaultencoding())

    def __unicode__(self):
        return six.text_type(self._record)

    def __repr__(self):
        return '<Karma Record \'{record}\'>'.format(
            record=six.text_type(self)
        )
