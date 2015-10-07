# -*- coding: utf-8 -*-
"""
    shipment.py
"""
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

        if not self.sales or self.state != 'done':
            return

        sale = self.sales[0]
        fulfilment_data = E.FulfillmentData(
            E.CarrierCode(self.carrier.carrier_cost_method),
            E.ShippingMethod(self.carrier.carrier_cost_method),
            E.ShipperTrackingNumber(self.tracking_number),
        )
        for move in self.outgoing_moves:
            if not isinstance(move.origin, SaleLine):
                continue
            fulfilment_data.append(E.Item(
                E.AmazonOrderItemCode(move.origin.channel_identifier),
                E.MerchantFulfillmentItemID(str(move.id)),
                E.Quantity(str(move.quantity))
            ))
        message = E.Message(
            E.MessageID(str(self.id)),
            E.OrderFulfillment(
                E.AmazonOrderID(sale.reference),
                E.MerchantFulfillmentID(self.code),
                E.FulfillmentDate(str(self.effective_date.isoformat())),
                fulfilment_data
            )
        )
        envelope_xml = sale.channel._get_amazon_envelop(
            'OrderFulfillment', message
        )
        feeds_api = sale.channel.get_amazon_feed_api()
        feeds_api.submit_feed(
            etree.tostring(envelope_xml),
            feed_type='_POST_ORDER_FULFILLMENT_DATA_',
            marketplaceids=[sale.channel.amazon_marketplace_id]
        )
