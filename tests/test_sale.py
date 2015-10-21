# -*- coding: utf-8 -*-
"""
    test_sale

    Tests Sale

"""
import os
import sys
import unittest
DIR = os.path.abspath(os.path.normpath(
    os.path.join(
        __file__,
        '..', '..', '..', '..', '..', 'trytond'
    )
))
if os.path.isdir(DIR):
    sys.path.insert(0, os.path.dirname(DIR))
from decimal import Decimal

import trytond.tests.test_tryton
from trytond.tests.test_tryton import POOL, USER, DB_NAME, CONTEXT
from test_base import TestBase, load_json
from trytond.transaction import Transaction


class TestSale(TestBase):
    """
    Tests import of sale order
    """

    def test_0010_create_sale_using_amazon_data_with_exception(self):
        """
        Tests creation of sale order using amazon data
        """
        Sale = POOL.get('sale.sale')
        Product = POOL.get('product.product')
        Party = POOL.get('party.party')
        ContactMechanism = POOL.get('party.contact_mechanism')
        ChannelException = POOL.get('channel.exception')
        Listing = POOL.get('product.product.channel_listing')

        with Transaction().start(DB_NAME, USER, CONTEXT):
            self.setup_defaults()

            with Transaction().set_context({
                'current_channel': self.sale_channel.id,
            }):

                orders = Sale.search([])
                self.assertEqual(len(orders), 0)

                order_data = load_json(
                    'orders', 'order_list'
                )['Orders']['Order']
                line_data = load_json(
                    'orders', 'order_items'
                )['OrderItems']['OrderItem']
                self.assertFalse(
                    Party.search([
                        ('name', '=', order_data['BuyerEmail']['value'])
                    ])
                )

                self.assertFalse(
                    ContactMechanism.search([
                        ('party.name', '=', order_data['BuyerEmail']['value']),
                        ('type', 'in', ['phone', 'mobile']),
                        ('value', '=',
                            order_data['ShippingAddress']['Phone']['value']),
                    ])
                )

                # Create product using sku
                product_data = load_json('products', 'product-1')
                product_data.update({
                    'Id': {
                        'value': line_data['SellerSKU']['value']
                    }
                })
                product = Product.create_from(self.sale_channel, product_data)

                Listing(
                    product=product,
                    channel=self.sale_channel,
                    product_identifier=line_data['SellerSKU']['value'],
                    asin=product_data['Products']['Product']['Identifiers']["MarketplaceASIN"]["ASIN"]["value"],  # noqa
                ).save()

                self.assertFalse(ChannelException.search([]))

                order_data['OrderTotal']['Amount']['value'] = Decimal('0.04')

                with Transaction().set_context(company=self.company.id):
                    order = Sale.create_using_amazon_data(order_data, line_data)

                self.assertNotEqual(
                    order.total_amount,
                    order_data['OrderTotal']['Amount']['value']
                )

                # Since order total does not match
                self.assertTrue(ChannelException.search([]))

                self.assertEqual(order.state, 'draft')

    def test_0015_create_sale_using_amazon_data_without_exception(self):
        """
        Tests creation of sale order using amazon data with equal sale total
        """
        Sale = POOL.get('sale.sale')
        Product = POOL.get('product.product')
        Party = POOL.get('party.party')
        ContactMechanism = POOL.get('party.contact_mechanism')
        ChannelException = POOL.get('channel.exception')
        Listing = POOL.get('product.product.channel_listing')
        ChannelState = POOL.get('sale.channel.order_state')

        with Transaction().start(DB_NAME, USER, CONTEXT):
            self.setup_defaults()

            with Transaction().set_context({
                'current_channel': self.sale_channel.id,
            }):

                orders = Sale.search([])
                self.assertEqual(len(orders), 0)

                order_data = load_json(
                    'orders', 'order_list'
                )['Orders']['Order']
                line_data = load_json(
                    'orders', 'order_items'
                )['OrderItems']['OrderItem']

                # Create Order State
                # TODO: Add tests to import order states
                ChannelState.create([{
                    'name': 'Shipped',
                    'code': 'Shipped',
                    'action': 'import_as_past',
                    'invoice_method': 'order',
                    'shipment_method': 'order',
                    'channel': self.sale_channel,
                }])

                self.assertFalse(
                    Party.search([
                        ('name', '=', order_data['BuyerEmail']['value'])
                    ])
                )

                self.assertFalse(
                    ContactMechanism.search([
                        ('party.name', '=', order_data['BuyerEmail']['value']),
                        ('type', 'in', ['phone', 'mobile']),
                        ('value', '=',
                            order_data['ShippingAddress']['Phone']['value']),
                    ])
                )

                # Create product using sku
                product_data = load_json('products', 'product-1')
                product_data.update({
                    'Id': {
                        'value': line_data['SellerSKU']['value']
                    }
                })
                product = Product.create_from(self.sale_channel, product_data)

                Listing(
                    product=product,
                    channel=self.sale_channel,
                    product_identifier=line_data['SellerSKU']['value'],
                    asin=product_data['Products']['Product']['Identifiers']["MarketplaceASIN"]["ASIN"]["value"],  # noqa
                ).save()

                self.assertFalse(ChannelException.search([]))

                with Transaction().set_context(company=self.company.id):
                    order = Sale.create_using_amazon_data(order_data, line_data)

                self.assertFalse(ChannelException.search([]))
                self.assertEqual(order.state, 'done')

                orders = Sale.search([('state', '=', 'done')])
                self.assertEqual(len(orders), 1)

                self.assertTrue(
                    Party.search([
                        ('name', '=', order_data['BuyerName']['value'])
                    ])
                )

                party, = Party.search([
                    ('name', '=', order_data['BuyerName']['value'])
                ])

                # Address is created for party
                self.assertEqual(len(party.addresses), 1)

                # Phone is added to party
                self.assertTrue(
                    ContactMechanism.search([
                        ('party', '=', party),
                        ('type', 'in', ['phone', 'mobile']),
                        ('value', '=',
                            order_data['ShippingAddress']['Phone']['value']),
                    ])
                )
                address, = party.addresses
                self.assertEqual(
                    address.name, order_data['ShippingAddress']['Name']['value']
                )

                # Item lines + shipping line should be equal to lines on tryton
                self.assertEqual(len(order.lines), 2)

    def test_0020_check_matched_address_using_amazon_data(self):
        """
        Tests address if same address already exists
        """
        Party = POOL.get('party.party')
        Address = POOL.get('party.address')

        with Transaction().start(DB_NAME, USER, CONTEXT):
            self.setup_defaults()

            with Transaction().set_context({
                'current_channel': self.sale_channel.id,
            }):

                order_data = load_json(
                    'orders', 'order_list'
                )['Orders']['Order']

                address_data = order_data['ShippingAddress']

                party = Party.create_using_amazon_data({
                    'name': order_data['BuyerEmail']['value'],
                    'email': order_data['BuyerName']['value'],
                })

                self.assertFalse(
                    Address.search([
                        ('name', '=', address_data['Name']['value'])
                    ])
                )

                # Add address for party
                Address.find_or_create_for_party_using_amazon_data(
                    party, order_data['ShippingAddress']
                )
                self.assertTrue(
                    Address.search([
                        ('name', '=', address_data['Name']['value'])
                    ])
                )
                self.assertEqual(
                    Address.search([
                        ('name', '=', address_data['Name']['value'])
                    ], count=True), 1
                )

                # Add same address for party
                Address.find_or_create_for_party_using_amazon_data(
                    party, order_data['ShippingAddress']
                )

                # Now new address is created
                self.assertEqual(
                    Address.search([
                        ('name', '=', address_data['Name']['value'])
                    ], count=True), 1
                )

    def test_0030_create_duplicate_party(self):
        """
        Tests duplicate party is created with same amazon email
        """
        Party = POOL.get('party.party')

        with Transaction().start(DB_NAME, USER, CONTEXT):
            self.setup_defaults()

            with Transaction().set_context({
                'current_channel': self.sale_channel.id,
            }):

                order_data = load_json(
                    'orders', 'order_list'
                )['Orders']['Order']

                self.assertFalse(
                    Party.search([
                        ('name', '=', order_data['BuyerEmail']['value'])
                    ])
                )

                party1 = Party.find_or_create_using_amazon_data({
                    'name': order_data['BuyerEmail']['value'],
                    'email': order_data['BuyerName']['value'],
                })

                self.assertTrue(
                    Party.search([
                        ('name', '=', order_data['BuyerEmail']['value'])
                    ])
                )
                self.assertEqual(
                    Party.search([
                        ('name', '=', order_data['BuyerEmail']['value'])
                    ], count=True), 1
                )

                # Create party with same email again and it wont create
                # new one
                party2 = Party.find_or_create_using_amazon_data({
                    'name': order_data['BuyerEmail']['value'],
                    'email': order_data['BuyerName']['value'],
                })

                self.assertEqual(party1, party2)

                self.assertEqual(
                    Party.search([
                        ('name', '=', order_data['BuyerEmail']['value'])
                    ], count=True), 1
                )


def suite():
    """
    Test Suite
    """
    test_suite = trytond.tests.test_tryton.suite()
    test_suite.addTests(
        unittest.TestLoader().loadTestsFromTestCase(TestSale)
    )
    return test_suite

if __name__ == '__main__':
    unittest.TextTestRunner(verbosity=2).run(suite())
