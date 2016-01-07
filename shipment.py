# -*- coding: utf-8 -*-
"""
    shipment.py
"""
from collections import defaultdict
from lxml.builder import E
from lxml import etree
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval, PYSONEncoder
from trytond.model import ModelView, fields, Workflow
from trytond.wizard import Wizard, StateAction, StateView, Button


__all__ = [
    'ShipmentOut', 'StockLocation', 'ShipmentInternal',
    'InboundShipmentProducts', 'InboundShipmentCreateStart',
    'InboundShipmentCreate'
]
__metaclass__ = PoolMeta


class StockLocation:
    __name__ = 'stock.location'

    # This field is added so we can have sku of the product (fullfilled by
    # amazon) while sending product info to amazon network
    channel = fields.Many2One(
        "sale.channel", "Channel", states={
            'required': Eval('subtype') == 'fba',
            'invisible': Eval('subtype') != 'fba',
        }, domain=[('source', '=', 'amazon_mws')],
        depends=['subtype']
    )

    @classmethod
    def __setup__(cls):
        """
        Setup the class before adding to pool
        """
        super(StockLocation, cls).__setup__()

        fba = ('fba', 'Fullfilled By Amazon')

        if fba not in cls.subtype.selection:
            cls.subtype.selection.append(fba)


class ShipmentOut:
    "ShipmentOut"
    __name__ = 'stock.shipment.out'

    def export_shipment_status_to_amazon(self):
        """
        TODO: This should be done in bulk to avoid over using the amazon
        API.
        """
        SaleLine = Pool().get('sale.line')
        if self.state != 'done':
            return

        # Handle the case where a shipment could have been merged
        # across channels or even two amazon accounts.
        items_by_sale = defaultdict(list)

        # Find carrier code and shipment method
        fulfilment_elements = []
        carrier_code = None
        shipping_method = 'Standard'

        if self.carrier.carrier_cost_method in ('endicia', ):
            carrier_code = 'USPS'
            shipping_method = self.endicia_mailclass.name
        elif self.carrier.carrier_cost_method == 'fedex':
            carrier_code = 'FedEx'
            shipping_method = self.fedex_service_type.name
        elif self.carrier.carrier_cost_method == 'ups':
            carrier_code = 'UPS'
            shipping_method = self.ups_service_type.name
        # TODO: Add GLS etc

        if carrier_code is None:
            fulfilment_elements.append(
                E.CarrierName(
                    self.carrier and self.carrier.rec_name or 'self'
                )
            )
        else:
            fulfilment_elements.append(
                E.CarrierCode(carrier_code)
            )

        fulfilment_elements.extend([
            E.ShippingMethod(shipping_method),
            E.ShipperTrackingNumber(self.tracking_number),
        ])
        fulfilment_data = E.FulfillmentData(*fulfilment_elements)

        # For all outgoing moves add items
        for move in self.outgoing_moves:
            if not move.quantity:
                # back order
                continue
            if not isinstance(move.origin, SaleLine):
                continue
            if move.origin.sale.channel.source != 'amazon_mws':
                continue
            items_by_sale[move.origin.sale].append(
                E.Item(
                    E.AmazonOrderItemCode(move.origin.channel_identifier),
                    E.Quantity(str(int(move.quantity)))
                )
            )

        # For each sale, now export the data
        for sale, items in items_by_sale.items():
            message = E.Message(
                E.MessageID(str(sale.id)),  # just has to be unique in envelope
                E.OrderFulfillment(
                    E.AmazonOrderID(sale.channel_identifier),
                    E.FulfillmentDate(
                        self.write_date.strftime('%Y-%m-%dT00:00:00Z')
                    ),
                    fulfilment_data,
                    *items
                )
            )
            envelope_xml = sale.channel._get_amazon_envelop(
                'OrderFulfillment', [message]
            )
            feeds_api = sale.channel.get_amazon_feed_api()
            feeds_api.submit_feed(
                etree.tostring(envelope_xml),
                feed_type='_POST_ORDER_FULFILLMENT_DATA_',
                marketplaceids=[sale.channel.amazon_marketplace_id]
            )


class ShipmentInternal:
    "Internal Shipment"
    __name__ = 'stock.shipment.internal'

    mws_inbound_shipment_id = fields.Char(
        "MWS Inbound Shipment ID", readonly=True,
        states={
            'invisible': Eval('channel_source') != 'amazon_mws'
        }, depends=['channel_source']
    )

    channel_source = fields.Function(
        fields.Char("Channel Source"), "on_change_with_channel_source"
    )

    @fields.depends('to_location')
    def on_change_with_channel_source(self, name=None):
        return (self.to_location.parent and self.to_location.parent.channel) \
            and self.to_location.parent.channel.source or None

    @classmethod
    @ModelView.button
    @Workflow.transition('done')
    def done(cls, shipments):
        for shipment in shipments:
            if not shipment.mws_inbound_shipment_id:
                continue

            channel = shipment.to_location.parent.channel
            channel.validate_amazon_channel()
            mws_connection_api = channel.get_mws_connection_api()

            # Get status for inbound shipment
            result = mws_connection_api.list_inbound_shipments(
                ShipmentIdList=[shipment.mws_inbound_shipment_id]
            )
            if result.ListInboundShipmentsResult.ShipmentData[0].ShipmentStatus != 'CLOSED':  # noqa
                cls.raise_user_error(
                    "Items in this shipments has not been received by "
                    "the Amazon fulfillment center yet."
                )
        return super(ShipmentInternal, cls).done(shipments)


class InboundShipmentProducts(ModelView):
    "Inbound Shipment Products"
    __name__ = 'inbound_shipment.products'

    product = fields.Many2One("product.product", "Product", required=True)
    quantity = fields.Integer("Quantity", required=True)


class InboundShipmentCreateStart(ModelView):
    "Create Inbound Shipment Start"
    __name__ = "inbound_shipment.create.start"

    from_location = fields.Many2One(
        'stock.location', "From Location", required=True, domain=[
            ('type', 'in', ['view', 'storage', 'lost_found']),
        ]
    )
    to_location = fields.Many2One(
        'stock.location', "To Location", required=True, domain=[
            ('type', 'in', ['view', 'storage', 'lost_found']),
            ('parent.subtype', '=', 'fba'),
            ('parent.channel.source', '=', 'amazon_mws'),
        ]
    )
    products = fields.One2Many(
        "inbound_shipment.products", None, "Product", required=True
    )


class InboundShipmentCreate(Wizard):
    "Create Inbound Shipment"
    __name__ = "inbound_shipment.create"

    start = StateView(
        'inbound_shipment.create.start',
        'amazon_mws.inbound_shipment_create_start_form',
        [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Create', 'create_', 'tryton-go-next', default=True),
        ]
    )
    create_ = StateAction('stock.act_shipment_internal_form')

    def find_product_using_sku(self, sku, channel):
        """
        Search product with given sku and channel
        """
        Listing = Pool().get('product.product.channel_listing')

        listings = Listing.search([
            ('channel', '=', channel.id),
            ['OR',
                [
                    ('fba_code', '=', sku)
                ], [
                    ('product.code', '=', sku)
                ]
             ]
        ], limit=1)

        return listings and listings[0].product or None

    def create_inbound_shipment(self):
        Listing = Pool().get('product.product.channel_listing')
        Shipment = Pool().get('stock.shipment.internal')

        to_warehouse = self.start.to_location.parent
        to_location = self.start.to_location
        from_location = self.start.from_location

        if to_warehouse.subtype != 'fba':
            return

        channel = to_warehouse.channel

        channel.validate_amazon_channel()

        mws_connection_api = channel.get_mws_connection_api()

        from_address = self.start.from_location.parent.address

        if not from_address:
            self.raise_user_error(
                "Warehouse %s must have an address" % (
                    to_location.parent.title()
                )
            )

        ship_from_address = from_address.to_fba()

        fba_products = []
        for inbound_product in self.start.products:
            listings = Listing.search([
                ('product', '=', inbound_product.product.id),
                ('channel', '=', channel.id)
            ], limit=1)
            if not listings:
                self.raise_user_error(
                    "Product %s is not listed on amazon" % (
                        inbound_product.product.rec_name
                    )
                )
            listing, = listings

            fba_code = listing.fba_code
            if not listing.fba_code:
                fba_code = inbound_product.product.code

            fba_products.append(
                (fba_code, inbound_product.quantity)
            )

        request_items = dict(Member=[{
            'SellerSKU': sku,
            'Quantity': str(int(qty)),
        } for sku, qty in fba_products])

        # Create Inbound shipment plan, that would return info
        # required to create inbound shipment
        try:
            plan_response = mws_connection_api.create_inbound_shipment_plan(
                ShipFromAddress=ship_from_address,
                InboundShipmentPlanRequestItems=request_items
            )
        except Exception, e:  # XXX: Handle InvalidRequestException
            self.raise_user_error(e.message)

        shipment_values = []
        for plan in plan_response.CreateInboundShipmentPlanResult.InboundShipmentPlans:  # noqa
            shipment_header = {
                'ShipmentName': plan.ShipmentId,
                'ShipFromAddress': ship_from_address,
                'DestinationFulfillmentCenterId':
                    plan.DestinationFulfillmentCenterId,
                'LabelPrepPreference': plan.LabelPrepType,
                'ShipmentStatus': 'WORKING',
            }
            shipment_items = dict(Member=[{
                'SellerSKU': item.SellerSKU,
                'QuantityShipped': item.Quantity,
            } for item in plan.Items])

            # Create inbound shipment for each item
            try:
                mws_connection_api.create_inbound_shipment(
                    ShipmentId=plan.ShipmentId,
                    InboundShipmentHeader=shipment_header,
                    InboundShipmentItems=shipment_items
                )
            except Exception, e:  # XXX: Handle InvalidRequestException
                self.raise_user_error(e.message)

            moves = []
            for item in plan.Items:

                # Find product for sku returned from amazon
                product = self.find_product_using_sku(item.SellerSKU, channel)
                moves.append({
                    'from_location': from_location,
                    'to_location': to_location,
                    'product': product.id,
                    'quantity': int(item.Quantity),
                    'uom': product.default_uom.id,
                })

            shipment_values.append({
                'from_location': from_location,
                'to_location': to_location,
                'mws_inbound_shipment_id': plan.ShipmentId,
                'moves': [('create', moves)],
            })

        shipments = Shipment.create(shipment_values)
        Shipment.wait(shipments)
        Shipment.assign(shipments)
        return shipments

    def do_create_(self, action):

        shipments = self.create_inbound_shipment()

        action['pyson_domain'] = PYSONEncoder().encode(
            [('id', 'in', map(int, shipments))]
        )
        action['name'] = "Inbound Shipments"

        return action, {}
