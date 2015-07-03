# -*- test-case-name: vumi.transports.whatsapp.tests.test_whatsapp -*-
from twisted.internet import defer
from twisted.internet import reactor
from twisted.internet.threads import deferToThread

from vumi.transports.base import Transport
from vumi.config import ConfigText, ConfigDict
from vumi import log
from vumi.message import TransportUserMessage
from vumi.persist.txredis_manager import TxRedisManager

from yowsup.stacks import YowStackBuilder

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
    redis_manager = ConfigDict(
        'How to connect to Redis', required=True, static=True)


class WhatsAppClientDone(Exception):
    """ Signal that the Yowsup client is done. """


class WhatsAppTransport(Transport):

    CONFIG_CLASS = WhatsAppTransportConfig
    transport_type = 'whatsapp'

    @defer.inlineCallbacks
    def setup_transport(self):
        config = self.get_static_config()
        log.info('Transport starting with: %s' % (config,))
        CREDENTIALS = (config.phone, config.password)

        stack_client = self.stack_client = StackClient(CREDENTIALS, self)
        self.client_d = deferToThread(stack_client.client_start)
        self.client_d.addErrback(self.catch_exit)
        self.client_d.addErrback(self.print_error)

        self.redis = yield TxRedisManager.from_config(config.redis_manager)
        self.redis = self.redis.sub_manager(self.transport_name)

    @defer.inlineCallbacks
    def teardown_transport(self):
        print "Stopping client ..."
        self.stack_client.client_stop()
        yield self.redis._close()
        yield self.client_d
        print "Loop done."

    def handle_outbound_message(self, message):
        # message is a vumi.message.TransportUserMessage
        log.info('Sending %r' % (message.to_json(),))
        self.stack_client.send_to_stack(
            message['content'], message['to_addr'], message['message_id'])
        # TODO: set the  remote-message-id  to something more useful
#        return self.publish_ack(
#            message['message_id'], 'remote-message-id')

    def catch_exit(self, f):
        f.trap(WhatsAppClientDone)
        print "Yowsup client killed."

    def print_error(self, f):
        print f
        return f


class StackClient(object):

    STACK_BUILDER = YowStackBuilder

    def __init__(self, credentials, transport):
        self.CREDENTIALS = credentials
        self.transport = transport

        self.stack = self.STACK_BUILDER.getDefaultStack(
            layer=WhatsAppInterface(transport), media=False)
        self.stack.setCredentials(self.CREDENTIALS)

        self.network_layer = self.stack.getLayer(0)
        self.whatsapp_interface = self.stack.getLayer(-1)

    def client_start(self):

        self.whatsapp_interface.connect()

        self.stack.loop(discrete=0, count=1, timeout=1)

    def client_stop(self):
        print "Stopping client ..."

        def _stop():
            print "Sending disconnect ..."
            self.whatsapp_interface.disconnect()

        def _kill():
            raise WhatsAppClientDone("We are exiting NOW!")

        self.stack.execDetached(_stop)
        self.stack.execDetached(_kill)

    def send_to_stack(self, text, to_address, message_id):
        def send():
            self.whatsapp_interface.send_to_human(text, to_address + '@s.whatsapp.net', message_id)
        self.stack.execDetached(send)


class WhatsAppInterface(YowInterfaceLayer):

    def __init__(self, transport):
        super(WhatsAppInterface, self).__init__()
        self.transport = transport

    def send_to_human(self, text, to_address, message_id):
        # message_id is vumi id
        message = TextMessageProtocolEntity(text, to=to_address)
        self.transport.redis.setex(message.getId(), 60 * 60 * 24, message_id)
        # new whatsapp id, message.getId(), set
        self.toLower(message)

    @ProtocolEntityCallback("message")
    def onMessage(self, messageProtocolEntity):
        from_address = messageProtocolEntity.getFrom(False)
        body = messageProtocolEntity.getBody()

        receipt = OutgoingReceiptProtocolEntity(
            messageProtocolEntity.getId(),
            from_address + '@s.whatsapp.net', 'read',
            messageProtocolEntity.getParticipant())

        self.toLower(receipt)
        reactor.callFromThread(self.transport.publish_message,
                               from_addr=from_address, content=body, to_addr=None,
                               transport_type=self.transport.transport_type,
                               to_addr_type=TransportUserMessage.AT_MSISDN)

    @ProtocolEntityCallback("receipt")
    def onReceipt(self, entity):
        '''receives confirmation of delivery to human'''
        # shady
        # getting too many receipts
        print 'got receipt'
        ack = OutgoingAckProtocolEntity(
            entity.getId(), "receipt", "delivery", entity.getFrom())
        self.toLower(ack)

    @ProtocolEntityCallback("ack")
    def onAck(self, ack):
        '''receives confirmation of delivery to server'''
        print 'got ack'
        # sent_message_id: whatsapp id
        # user_message_id: vumi_id
        msg_id = ack.getId()
        reactor.callFromThread(self.transport.publish_ack,
                               user_message_id=self.transport.redis.get(msg_id),
                               sent_message_id=msg_id)
