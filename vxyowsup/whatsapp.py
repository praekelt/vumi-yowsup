# -*- test-case-name: vumi.transports.whatsapp.tests.test_whatsapp -*-
from twisted.internet import defer
from twisted.internet.threads import deferToThread

from vumi.transports.base import Transport
from vumi.config import ConfigText
from vumi import log

from yowsup.layers.network import YowNetworkLayer

from yowsup.common import YowConstants
from yowsup.layers import YowLayerEvent
from yowsup.stacks import YowStack, YowStackBuilder
from yowsup import env

import sys
import argparse
import yowsup
import logging

from yowsup.layers.interface import YowInterfaceLayer, ProtocolEntityCallback
from yowsup.layers.protocol_messages.protocolentities import TextMessageProtocolEntity
from yowsup.layers.protocol_receipts.protocolentities import OutgoingReceiptProtocolEntity
from yowsup.layers.protocol_acks.protocolentities import OutgoingAckProtocolEntity


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


class WhatsAppClientDone(Exception):
    """ Signal that the Yowsup client is done. """


class WhatsAppTransport(Transport):

    CONFIG_CLASS = WhatsAppTransportConfig
    transport_type = 'whatsapp'

    def setup_transport(self):
        config = self.get_static_config()
        log.info('Transport starting with: %s' % (config,))
        CREDENTIALS = (config.phone, config.password)

        stack_client = self.stack_client = StackClient(CREDENTIALS)

        self.client_d = deferToThread(stack_client.client_start)
        self.client_d.addErrback(self.catch_exit)
        self.client_d.addErrback(self.print_error)

    @defer.inlineCallbacks
    def teardown_transport(self):
        print "Stopping client ..."
        self.stack_client.client_stop()
        yield self.client_d
        print "Loop done."

    def handle_outbound_message(self, message):
        # message is a vumi.message.TransportUserMessage
        log.info('Sending %r' % (message.to_json(),))
        if message['content'] == 'fail!':
            return self.publish_nack(message['message_id'], 'failed')
            return self.publish_ack(
                message['message_id'], 'remote-message-id')
        # assumes message['to_addr'] will be the phone number
        self.stack_client.send_to_stack(
            message['content'], message['to_addr'] + '@s.whatsapp.net')

    def catch_exit(self, f):
        f.trap(WhatsAppClientDone)
        print "Yowsup client killed."

    def print_error(self, f):
        print f
        return f


class StackClient(object):

    def __init__(self, credentials):
        self.CREDENTIALS = credentials

    def client_start(self):
        self.stack = YowStackBuilder.getDefaultStack(
            layer=EchoLayer, media=False)
        self.stack.setCredentials(self.CREDENTIALS)
        self.network_layer = self.stack.getLayer(0)
        self.echo_layer = self.stack.getLayer(-1)

        self.stack.broadcastEvent(YowLayerEvent(
            YowNetworkLayer.EVENT_STATE_CONNECT))

        self.stack.loop(discrete=0, count=1, timeout=1)

    def client_stop(self):
        print "Stopping client ..."

        def _stop():
            print "Sending disconnect ..."
            self.stack.broadcastEvent(YowLayerEvent(
                YowNetworkLayer.EVENT_STATE_DISCONNECT))

        def _kill():
            raise WhatsAppClientDone("We are exiting NOW!")

        self.stack.execDetached(_stop)
        self.stack.execDetached(_kill)

    def send_to_stack(self, text, to_address):
        def send():
            self.echo_layer.send_to_human(text, to_address)
        self.stack.execDetached(send)


class EchoLayer(YowInterfaceLayer):

    def send_to_human(self, text, to_address):
        message = TextMessageProtocolEntity(text, to=to_address)
        self.toLower(message)
        print(text, to_address)

    @ProtocolEntityCallback("message")
    def onMessage(self, messageProtocolEntity):
        receipt = OutgoingReceiptProtocolEntity(
            messageProtocolEntity.getId(),
            messageProtocolEntity.getFrom(), 'read',
            messageProtocolEntity.getParticipant())

        self.toLower(receipt)

        self.send_to_human(text=messageProtocolEntity.getBody(),
                           to_address=messageProtocolEntity.getFrom())

    @ProtocolEntityCallback("receipt")
    def onReceipt(self, entity):
        ack = OutgoingAckProtocolEntity(
            entity.getId(), "receipt", "delivery", entity.getFrom())
        self.toLower(ack)
