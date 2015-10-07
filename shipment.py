# -*- coding: utf-8 -*-
"""
    shipment.py
"""
from collections import defaultdict
from lxml.builder import E
from lxml import etree
from trytond.pool import PoolMeta, Pool


__all__ = ['ShipmentOut']
__metaclass__ = PoolMeta


class ShipmentOut:
    "ShipmentOut"
    __name__ = 'stock.shipment.out'

    def export_shipment_status_to_amazon(self):
        SaleLine = Pool().get('sale.line')

        if self.state != 'done':
            return

        # Handle the case where a shipment could have been merged
        # across channels or even two amazon accounts.
        items_by_sale = defaultdict(list)

        # Fulfilment data remains the same
        fulfilment_data = E.FulfillmentData(
            E.CarrierCode(self.carrier.carrier_cost_method),
            E.ShippingMethod(self.carrier.carrier_cost_method),
            E.ShipperTrackingNumber(self.tracking_number),
        )

        for move in self.outgoing_moves:
            if not move.quantity:
                # back order
                continue
            if not isinstance(move.origin, SaleLine):
                continue
            if move.origin.channel.source != 'amazon_mws':
                continue
            items_by_sale[move.origin.sale].append(
                E.Item(
                    E.AmazonOrderItemCode(move.origin.channel_identifier),
                    E.MerchantFulfillmentItemID(str(move.id)),
                    E.Quantity(str(move.quantity))
                )
            )

        for sale, items in items_by_sale.items():
            message = E.Message(
                E.MessageID(str(sale.id)),  # just has to be unique in envelope
                E.OrderFulfillment(
                    E.AmazonOrderID(sale.channel_identifier),
                    E.MerchantFulfillmentID(self.code),
                    E.FulfillmentDate(str(self.effective_date.isoformat())),
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
