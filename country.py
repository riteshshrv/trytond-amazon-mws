# -*- coding: utf-8 -*-
"""
    country

    Country

"""
from trytond.pool import PoolMeta


__all__ = ['Subdivision']
__metaclass__ = PoolMeta


class Subdivision:
    "Subdivision"
    __name__ = 'country.subdivision'

    @classmethod
    def search_using_amazon_state(cls, value, country):
        """
        Searches for state with given amazon StateOrRegion value.

        :param value: Code or Name of state from amazon
        :param country: Active record of country
        :return: Active record of state if found else raises error
        """
        subdivisions = cls.search([
            ('country', '=', country.id),
            ('code', '=', country.code + '-' + value.upper())
        ])

        if not subdivisions:
            subdivisions = cls.search([
                ('country', '=', country.id),
                ('name', 'ilike', value),
            ], limit=1)

        if not subdivisions:
            return None

        return subdivisions[0]
