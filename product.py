# -*- coding: UTF-8 -*-
'''
    product

'''
from decimal import Decimal
from lxml import etree
from lxml.builder import E
from collections import defaultdict

from trytond.model import fields
from trytond.transaction import Transaction
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval


__all__ = [
    'Product', 'ProductCode', 'Template', 'ProductSaleChannelListing',
]
__metaclass__ = PoolMeta


class Template:
    "Product Template"
    __name__ = 'product.template'

    export_to_amazon = fields.Boolean('Amazon Exportable')


class Product:
    "Product"
    __name__ = "product.product"

    asin = fields.Function(fields.Many2One(
        'product.product.code', 'ASIN'
    ), 'get_codes')
    ean = fields.Function(fields.Many2One(
        'product.product.code', 'EAN'
    ), 'get_codes')
    upc = fields.Function(fields.Many2One(
        'product.product.code', 'UPC'
    ), 'get_codes')
    isbn = fields.Function(fields.Many2One(
        'product.product.code', 'ISBN'
    ), 'get_codes')
    gtin = fields.Function(fields.Many2One(
        'product.product.code', 'GTIN'
    ), 'get_codes')

    @classmethod
    def get_codes(cls, products, names):
        ProductCode = Pool().get('product.product.code')

        res = {}
        for name in names:
            res[name] = {}
            for product in products:
                code = ProductCode.search([
                    ('product', '=', product.id),
                    ('code_type', '=', name)
                ])
                res[name][product.id] = code and code[0].id or None

        return res

    @classmethod
    def extract_product_values_from_amazon_data(cls, product_attributes):
        """
        Extract product values from the amazon data, used for
        creation of product. This method can be overwritten by
        custom modules to store extra info to a product

        :param product_data: Product data from amazon
        :returns: Dictionary of values
        """
        SaleChannel = Pool().get('sale.channel')

        amazon_channel = SaleChannel(
            Transaction().context['current_channel']
        )
        assert amazon_channel.source == 'amazon_mws'

        return {
            'name': product_attributes['Title']['value'],
            'default_uom': amazon_channel.default_uom.id,
            'salable': True,
            'sale_uom': amazon_channel.default_uom.id,
        }

    @classmethod
    def create_from(cls, channel, product_data):
        """
        Create the product for the channel
        """
        if channel.source != 'amazon_mws':
            return super(Product, cls).create_from(channel, product_data)
        return cls.create_using_amazon_data(product_data)

    @classmethod
    def create_using_amazon_data(cls, product_data):
        """
        Create a new product with the `product_data` from amazon.

        :param product_data: Product Data from Amazon
        :returns: Active record of product created
        """
        Template = Pool().get('product.template')

        # TODO: Handle attribute sets in multiple languages
        product_attribute_set = product_data['Products']['Product'][
            'AttributeSets'
        ]
        if isinstance(product_attribute_set, dict):
            product_attributes = product_attribute_set['ItemAttributes']
        else:
            product_attributes = product_attribute_set[0]['ItemAttributes']

        product_values = cls.extract_product_values_from_amazon_data(
            product_attributes
        )

        product_values.update({
            'products': [('create', [{
                'code': product_data['Id']['value'],
                'list_price': Decimal('0.01'),
                'cost_price': Decimal('0.01'),
                'description': product_attributes['Title']['value'],
            }])],
        })

        product_template, = Template.create([product_values])

        return product_template.products[0]


class ProductCode:
    "Amazon Product Identifier"
    __name__ = 'product.product.code'

    @classmethod
    def __setup__(cls):
        """
        Setup the class before adding to pool
        """
        super(ProductCode, cls).__setup__()
        cls.code_type.selection.extend([
            ('upc', 'UPC'),
            ('isbn', 'ISBN'),
            ('asin', 'ASIN'),
            ('gtin', 'GTIN')
        ])


class ProductSaleChannelListing:
    "Product Sale Channel"
    __name__ = 'product.product.channel_listing'

    asin = fields.Char('ASIN', states={
        'required': Eval('channel_source') == 'amazon_mws',
        'invisible': Eval('channel_source') != 'amazon_mws',
    }, depends=['channel_source'])

    def export_inventory(self):
        """
        Export inventory of this listing to external channel
        """
        if self.channel.source != 'amazon_mws':
            return super(ProductSaleChannelListing, self).export_inventory()

        self.export_bulk_inventory([self])

    @classmethod
    def export_bulk_inventory(cls, listings):
        """
        bulk export inventory to amazon
        """
        if not listings:
            # Nothing to update
            return

        non_amazon_listings = cls.search([
            ('id', 'in', map(int, listings)),
            ('channel.source', '!=', 'amazon_mws'),
        ])
        if non_amazon_listings:
            super(ProductSaleChannelListing, cls).export_bulk_inventory(
                non_amazon_listings
            )
        amazon_listings = filter(
            lambda l: l not in non_amazon_listings, listings
        )

        inventory_channel_map = defaultdict(list)
        for listing in amazon_listings:
            product = listing.product
            channel = listing.channel

            # group inventory xml by channel
            inventory_channel_map[channel].append(E.Message(
                E.MessageID(str(product.id)),
                E.OperationType('Update'),
                E.Inventory(
                    E.SKU(listing.product_identifier),
                    E.Quantity(str(round(listing.quantity))),
                    E.FulfillmentLatency(
                        str(product.delivery_time)
                    ),
                )
            ))

        for channel, elements in inventory_channel_map.iteritems():
            envelope_xml = channel._get_amazon_envelop('Inventory', elements)

            feeds_api = channel.get_amazon_feed_api()

            feeds_api.submit_feed(
                etree.tostring(envelope_xml),
                feed_type='_POST_INVENTORY_AVAILABILITY_DATA_',
                marketplaceids=[channel.amazon_marketplace_id]
            )
