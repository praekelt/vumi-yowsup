# -*- test-case-name: vumi.transports.whatsapp.tests.test_whatsapp -*-
from twisted.internet import defer
from twisted.internet import reactor
from twisted.internet.threads import deferToThread

from vumi.transports.base import Transport
from vumi.config import ConfigText, ConfigDict, ConfigInt
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
    ack_timeout = ConfigInt(
        'Length of time (integer) redis will store message ids in seconds (timeout for receiving acks)',
        default=60*60*24, static=True)


class WhatsAppClientDone(Exception):
    """ Signal that the Yowsup client is done. """


class WhatsAppTransport(Transport):

    CONFIG_CLASS = WhatsAppTransportConfig
    transport_type = 'whatsapp'

    @defer.inlineCallbacks
    def setup_transport(self):
        config = self.config = self.get_static_config()
        log.info('Transport starting with: %s' % (config,))

        self.redis = yield TxRedisManager.from_config(config.redis_manager)
        self.redis = self.redis.sub_manager(self.transport_name)

        CREDENTIALS = (config.phone, config.password)

        stack_client = self.stack_client = StackClient(CREDENTIALS, self)
        self.client_d = deferToThread(stack_client.client_start)
        self.client_d.addErrback(self.catch_exit)
        self.client_d.addErrback(self.print_error)

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

    @defer.inlineCallbacks
    def _send_ack(self, whatsapp_id):
        vumi_id = yield self.redis.get(whatsapp_id)
        yield self.publish_ack(user_message_id=vumi_id, sent_message_id=whatsapp_id)

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
        self.transport.redis.setex(message.getId(), self.transport.config.ack_timeout, message_id)
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

        print "You have received a message, and thusly sent a receipt"
        # print "You are now sending a reply"
        # self.send_to_human('iiii', from_address + '@s.whatsapp.net', "this transport's gone rogue")

        reactor.callFromThread(self.transport.publish_message,
                               from_addr=from_address, content=body, to_addr=None,
                               transport_type=self.transport.transport_type,
                               to_addr_type=TransportUserMessage.AT_MSISDN)

    @ProtocolEntityCallback("receipt")
    def onReceipt(self, entity):
        '''receives confirmation of delivery to human'''
        # shady?
        print "The user you attempted to contact has received the message"
        print "You are sending an acknowledgement of their accomplishment"
        ack = OutgoingAckProtocolEntity(entity.getId(), "receipt", entity.getType(), entity.getFrom())
        self.toLower(ack)

    @ProtocolEntityCallback("ack")
    def onAck(self, ack):
        '''receives confirmation of delivery to server'''
        # sent_message_id: whatsapp id
        # user_message_id: vumi_id
        print "WhatsApp acknowledges your " + ack.getClass()
        if ack.getClass() == "message":
            msg_id = ack.getId()
            reactor.callFromThread(self.transport._send_ack, msg_id)
