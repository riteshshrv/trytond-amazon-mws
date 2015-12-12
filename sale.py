# -*- coding: utf-8 -*-
"""
    sale

    Sale

"""
import dateutil.parser
from decimal import Decimal

from trytond.transaction import Transaction
from trytond.pool import PoolMeta, Pool
from trytond.exceptions import UserError


__all__ = ['Sale']
__metaclass__ = PoolMeta


class Sale:
    "Sale"
    __name__ = 'sale.sale'

    @classmethod
    def find_or_create_using_amazon_id(cls, order_id):
        """
        This method tries to find the sale with the order ID
        first and if not found it will fetch the info from amazon and
        create a new sale with the data from amazon using
        create_using_amazon_data

        :param order_id: Order ID from amazon
        :type order_id: string
        :returns: Active record of sale order created/found
        """
        SaleChannel = Pool().get('sale.channel')

        sales = cls.search([
            ('channel_identifier', '=', order_id),
        ])
        if sales:
            return sales[0]

        amazon_channel = SaleChannel(
            Transaction().context['current_channel']
        )
        assert amazon_channel.source == 'amazon_mws'

        order_api = amazon_channel.get_amazon_order_api()

        order_data = order_api.get_order([order_id]).parsed

        order_line_data = order_api.list_order_items(
            order_data['Orders']['Order']['AmazonOrderId']['value']
        ).parsed

        return cls.create_using_amazon_data(
            order_data['Orders']['Order'],
            order_line_data['OrderItems']['OrderItem']
        )

    @classmethod
    def create_using_amazon_data(cls, order_data, line_data):
        """
        Create a sale using amazon data

        :param order_data: Order data from amazon
        :return: Active record of record created
        """
        Party = Pool().get('party.party')
        Address = Pool().get('party.address')
        SaleChannel = Pool().get('sale.channel')
        ChannelException = Pool().get('channel.exception')

        amazon_channel = SaleChannel(
            Transaction().context['current_channel']
        )
        assert amazon_channel.source == 'amazon_mws'

        party_values = {
            'name': order_data['BuyerName']['value'],
            'email': order_data['BuyerEmail']['value'],
        }
        party = Party.find_or_create_using_amazon_data(party_values)
        if 'Phone' in order_data['ShippingAddress']:
            party.add_phone_using_amazon_data(
                order_data['ShippingAddress']['Phone']['value']
            )
        party_invoice_address = party_shipping_address = \
            Address.find_or_create_for_party_using_amazon_data(
                party, order_data['ShippingAddress']
            )

        sale = cls.get_sale_using_amazon_data(order_data, line_data)

        sale.party = party.id
        sale.invoice_address = party_invoice_address.id
        sale.shipment_address = party_shipping_address.id
        sale.channel = amazon_channel.id

        if order_data['FulfillmentChannel']['value'] == 'AFN':
            sale.warehouse = amazon_channel.fba_warehouse.id

        sale.save()

        # TODO: Handle Discounts
        # TODO: Handle Taxes

        if sale.total_amount != Decimal(
            order_data['OrderTotal']['Amount']['value']
        ):
            ChannelException.create([{
                'origin': '%s,%s' % (sale.__name__, sale.id),
                'log': 'Order total does not match.',
                'channel': sale.channel.id,
            }])

            return sale

        # Process sale now
        tryton_action = amazon_channel.get_tryton_action(
            order_data['OrderStatus']['value']
        )
        try:
            sale.process_to_channel_state(order_data['OrderStatus']['value'])
        except UserError, e:
            # Expecting UserError will only come when sale order has
            # channel exception.
            # Just ignore the error and leave this order in draft state
            # and let the user fix this manually.
            ChannelException.create([{
                'origin': '%s,%s' % (sale.__name__, sale.id),
                'log': "Error occurred on transitioning to state %s.\nError "
                "Message: %s" % (tryton_action['action'], e.message),
                'channel': sale.channel.id,
            }])

        return sale

    @classmethod
    def get_sale_using_amazon_data(cls, order_data, line_data):
        """
        Returns sale for amazon order
        """
        Sale = Pool().get('sale.sale')
        Currency = Pool().get('currency.currency')
        currency, = Currency.search([
            ('code', '=', order_data['OrderTotal']['CurrencyCode']['value'])
        ], limit=1)

        return Sale(
            reference=order_data['AmazonOrderId']['value'],
            sale_date=dateutil.parser.parse(
                order_data['PurchaseDate']['value']
            ).date(),
            currency=currency.id,
            lines=cls.get_item_line_data_using_amazon_data(
                order_data, line_data
            ),
            channel_identifier=order_data['AmazonOrderId']['value'],
        )

    @classmethod
    def get_item_line_data_using_amazon_data(cls, order_data, line_data):
        """
        Make data for an item line from the amazon data.

        :param order_items: Order items
        :return: List of data of order lines in required format
        """
        SaleLine = Pool().get('sale.line')
        Channel = Pool().get('sale.channel')

        # Order lines are returned as dictionary for single record and as list
        # for mulitple reocrds.
        # So convert to list if its dictionary
        if isinstance(line_data, dict):
            # If its a single line order, then the array will be dict
            order_items = [line_data]
        else:
            # In case of multi line orders, the transaction array will be
            # a list of dictionaries
            order_items = line_data

        sale_lines = []

        amazon_channel = Channel(
            Transaction().context['current_channel']
        )
        amazon_channel.validate_amazon_channel()
        for order_item in order_items:
            quantity = Decimal(order_item['QuantityOrdered']['value'])
            promotion_discount = Decimal(
                order_item['PromotionDiscount']['Amount']['value']
                if 'PromotionDiscount' in order_item else 0
            )
            if quantity == 0:
                # XXX: If item is cancelled then quantity will be 0 and
                # item price will not be there.
                amount = 0
                unit_price = 0
            else:
                # TODO: Show promotion discount in sale order
                amount = Decimal(order_item['ItemPrice']['Amount']['value']) - \
                    promotion_discount
                # TODO: Amazon doesn't send unit_price. This is the only way to
                # calculate unit_price. Fix this if you have better.
                unit_price = amount / quantity
            product_data = {
                'FulfillmentChannel': order_data['FulfillmentChannel']['value'],
                'ASIN': line_data['ASIN']['value']
            }
            sale_lines.append(
                SaleLine(
                    description=order_item['Title']['value'],
                    unit_price=unit_price,
                    unit=amazon_channel.default_uom.id,
                    quantity=quantity,
                    product=amazon_channel.get_product(
                        order_item['SellerSKU']['value'],
                        product_data,
                    ).id,
                    channel_identifier=order_item['OrderItemId']['value'],
                )
            )

            if order_item.get('ShippingPrice') and \
               order_item['ShippingPrice']['Amount']['value']:
                sale_lines.append(
                    cls.get_shipping_line_data_using_amazon_data(
                        order_data, order_item
                    )
                )

        return sale_lines

    @classmethod
    def get_shipping_line_data_using_amazon_data(cls, order_data, order_item):
        """
        Create a shipping line for the given sale using amazon data

        :param order_item: Order Data from amazon
        """
        SaleLine = Pool().get('sale.line')
        Channel = Pool().get('sale.channel')

        amazon_channel = Channel(
            Transaction().context['current_channel']
        )

        shipping_price = Decimal(
            order_item['ShippingPrice']['Amount']['value']
        )
        shipping_discount = Decimal(
            order_item['ShippingDiscount']['Amount']['value']
        )

        shipping_description = 'Amazon Shipping and Handling'
        if order_data.get('ShipServiceLevel'):
            shipping_description += "\nShipServiceLevel: %s" % order_data['ShipServiceLevel']['value']  # noqa

        if order_data.get('ShipmentServiceLevelCategory'):
            shipping_description += "\nShipmentServiceLevelCategory: %s" % order_data['ShipmentServiceLevelCategory']['value']  # noqa

        return SaleLine(
            description=shipping_description,
            unit_price=(shipping_price - shipping_discount),
            unit=amazon_channel.default_uom.id,
            quantity=1
        )

    def update_order_status_from_amazon_mws(self, order_data=None):
        """Update order status from amazon mws

        :TODO: this only handles shipped orders of amazon mws. Should handle
        other states too?
        """
        Shipment = Pool().get('stock.shipment.out')

        if order_data is None:
            order_api = self.channel.get_amazon_order_api()
            order_data = order_api.get_order(
                [self.channel_identifier]
            ).parsed['Orders']['Order']

        if order_data['OrderStatus']['value'] == "Canceled":
            # TODO
            # If not done
            # - cancel shipment
            # - cancel invoice or credit invoice
            pass

        if order_data['OrderStatus']['value'] == "Shipped":
            # Order is completed on amazon, process shipments and
            # invoices.
            for shipment in self.shipments:
                if shipment.state == 'draft':
                    Shipment.wait([shipment])
                if shipment.state == 'waiting':
                    Shipment.assign([shipment])
                if shipment.state == 'assigned':
                    Shipment.pack([shipment])
                if shipment.state == 'packed':
                    Shipment.done([shipment])

            # TODO: handle invoices?
