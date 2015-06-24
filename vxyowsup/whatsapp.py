# -*- test-case-name: vumi.transports.whatsapp.tests.test_whatsapp -*-
from twisted.internet import defer
from twisted.internet.threads import deferToThread

from vumi.transports.base import Transport
from vumi.config import ConfigText
from vumi import log

from yowsup.layers.network                             import YowNetworkLayer

from yowsup.common import YowConstants
from yowsup.layers import YowLayerEvent
from yowsup.stacks import YowStack, YowStackBuilder
from yowsup import env

import sys, argparse, yowsup, logging

from yowsup.layers.interface                           import YowInterfaceLayer, ProtocolEntityCallback
from yowsup.layers.protocol_messages.protocolentities  import TextMessageProtocolEntity
from yowsup.layers.protocol_receipts.protocolentities  import OutgoingReceiptProtocolEntity
from yowsup.layers.protocol_acks.protocolentities      import OutgoingAckProtocolEntity


class WhatsAppTransportConfig(Transport.CONFIG_CLASS):
    cc = ConfigText(
        'Country code of host phone number',
        default='27', static=True)
    phone = ConfigText(
        'Phone number, excluding "+", including country code',
        static=True)
    password = ConfigText(
        'Password received from WhatsApp on yowsup registration',
        static=True)


class WhatsAppTransport(Transport):

    CONFIG_CLASS = WhatsAppTransportConfig
    transport_type = 'whatsapp'

    def setup_transport(self):
        config = self.get_static_config()
        log.info('Transport starting with: %s' % (config,))
        CREDENTIALS = (config.phone, config.password)

        client = self.client = Client(CREDENTIALS)

        self.client_d = deferToThread(client.client_start)
        self.client_d.addErrback(self.print_error)

    def teardown_transport(self):
        print "Stopping client ..."
        self.client.client_stop()
        print "Stop sent."
        print "Waiting for asyncore loop to exit ..."
        yield self.client_d
        print "Loop done."

    def handle_outbound_message(self, message):
        # message is a vumi.message.TransportUserMessage
        log.info('Sending %r' % (message.to_json(),))
        if message['content'] == 'fail!':
            return self.publish_nack(message['message_id'], 'failed')
            return self.publish_ack(
                message['message_id'], 'remote-message-id')

    def print_error(self, f):
        print f
        return f


class Client(object):
    def __init__(self, credentials):
        self.CREDENTIALS = credentials

    def client_start(self):
        self.stack = YowStackBuilder.getDefaultStack(
            layer=EchoLayer, media=False)
        self.stack.setCredentials(self.CREDENTIALS)
        self.network_layer = self.stack.getLayer(0)

        self.stack.broadcastEvent(YowLayerEvent(
            YowNetworkLayer.EVENT_STATE_CONNECT))
        self.stack.loop(discrete=0, timeout=1)

    def client_stop(self):
        print "Stopping client ..."
        self.stack.broadcastEvent(YowLayerEvent(
            YowNetworkLayer.EVENT_STATE_DISCONNECT))


class EchoLayer(YowInterfaceLayer):

    @ProtocolEntityCallback("message")
    def onMessage(self, messageProtocolEntity):
        receipt = OutgoingReceiptProtocolEntity(
            messageProtocolEntity.getId(),
            messageProtocolEntity.getFrom(), 'read',
            messageProtocolEntity.getParticipant())

        outgoingMessageProtocolEntity = TextMessageProtocolEntity(
            messageProtocolEntity.getBody(),
            to=messageProtocolEntity.getFrom())

        self.toLower(receipt)

        self.toLower(outgoingMessageProtocolEntity)
        print(messageProtocolEntity.getBody())

    @ProtocolEntityCallback("receipt")
    def onReceipt(self, entity):
        ack = OutgoingAckProtocolEntity(
            entity.getId(), "receipt", "delivery", entity.getFrom())
        self.toLower(ack)
