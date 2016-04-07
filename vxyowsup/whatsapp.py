# -*- test-case-name: vumi.transports.whatsapp.tests.test_whatsapp -*-
from twisted.internet import defer, reactor
from twisted.internet.threads import deferToThread

from vumi.transports.base import Transport
from vumi.config import ConfigText, ConfigDict, ConfigInt
from vumi.message import TransportUserMessage
from vumi.persist.txredis_manager import TxRedisManager
from vumi.utils import StatusEdgeDetector

from yowsup.stacks import YowStackBuilder

from yowsup.layers.interface import YowInterfaceLayer, ProtocolEntityCallback
from yowsup.layers.protocol_messages.protocolentities import (
    TextMessageProtocolEntity)
from yowsup.layers.protocol_receipts.protocolentities import (
    OutgoingReceiptProtocolEntity)
from yowsup.layers.protocol_acks.protocolentities import (
    OutgoingAckProtocolEntity)
from yowsup.layers.network import YowNetworkLayer


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
        'Length of time (integer) redis will store message ids in seconds '
        '(timeout for receiving acks)',
        default=60*60*24, static=True)
    echo_to = ConfigText(
        'Echo messages received by transport to given MSISDN', static=True)


class WhatsAppClientDone(Exception):
    """ Signal that the Yowsup client is done. """


def msisdn_to_whatsapp(msisdn):
    """ Convert an MSISDN to a WhatsApp address. """
    return msisdn.lstrip('+') + '@s.whatsapp.net'


class WhatsAppTransport(Transport):

    CONFIG_CLASS = WhatsAppTransportConfig
    transport_type = 'whatsapp'

    @defer.inlineCallbacks
    def setup_transport(self):
        config = self.config = self.get_static_config()
        self.log.info('Transport starting with: %s' % (config,))

        self.redis = yield TxRedisManager.from_config(config.redis_manager)
        self.redis = self.redis.sub_manager(self.transport_name)

        self.our_msisdn = "+" + config.phone
        CREDENTIALS = (config.phone, config.password)

        self.stack_client = StackClient(CREDENTIALS, self)
        self.client_d = deferToThread(self.stack_client.client_start)
        self.client_d.addErrback(self.catch_exit)
        self.client_d.addErrback(self.log_error)

        self.status_detect = StatusEdgeDetector()

        # Wait for the WhatsApp client to connect before continuing.
        yield self.stack_client.connect_d

    @defer.inlineCallbacks
    def teardown_transport(self):
        self.log.info("Stopping client ...")
        if hasattr(self, 'stack_client'):
            self.stack_client.client_stop()
            yield self.client_d

        if hasattr(self, 'redis'):
            yield self.redis._close()

        self.log.info("Loop done.")

    def add_status(self, **kw):
        '''Publishes a status if it is not a repeat of the previously
        published status.'''
        if self.status_detect.check_status(**kw):
            return self.publish_status(**kw)
        return defer.succeed(None)

    def handle_outbound_message(self, message):
        # message is a vumi.message.TransportUserMessage
        self.log.info('Sending message: %s' % (message.to_json(),))
        msg = TextMessageProtocolEntity(
            message['content'].encode("UTF-8"),
            to=msisdn_to_whatsapp(message['to_addr']).encode("UTF-8"))
        self.redis.setex(
            msg.getId(), self.config.ack_timeout, message['message_id'])
        self.stack_client.send_to_stack(msg)

    @defer.inlineCallbacks
    def _send_ack(self, whatsapp_id):
        vumi_id = yield self.redis.get(whatsapp_id)
        yield self.publish_ack(
            user_message_id=vumi_id, sent_message_id=whatsapp_id)

    @defer.inlineCallbacks
    def _send_delivery_report(self, whatsapp_id):
        vumi_id = yield self.redis.get(whatsapp_id)
        if vumi_id:
            yield self.publish_delivery_report(
                user_message_id=vumi_id, delivery_status='delivered')
            yield self.redis.delete(whatsapp_id)

    def catch_exit(self, f):
        f.trap(WhatsAppClientDone)
        self.log.info("Yowsup client killed.")

    def log_error(self, f):
        self.log.error(f)
        return f

    def handle_inbound_error(self, error, message):
        return self.add_status(
            component='inbound', status='degraded', type='inbound_error',
            message=error, details={'message': message})

    def handle_inbound_success(self):
        return self.add_status(
            component='inbound', status='ok', type='inbound_success',
            message='Inbound message successfully processed')

    def handle_connected(self):
        return self.add_status(
            component='connection', status='ok', type='connected',
            message='Successfully connected to server')

    def handle_disconnected(self, reason):
        return self.add_status(
            component='connection', status='down', type='disconnected',
            message=reason)


class StackClient(object):

    STACK_BUILDER = YowStackBuilder

    def __init__(self, credentials, transport):
        self.CREDENTIALS = credentials
        self.transport = transport

        self.stack = self.STACK_BUILDER.getDefaultStack(
            layer=WhatsAppInterface(transport), media=False, axolotl=True)
        self.stack.setCredentials(self.CREDENTIALS)

        self.network_layer = self.stack.getLayer(0)
        self.whatsapp_interface = self.stack.getLayer(-1)
        self.connect_d = defer.Deferred()

    def client_start(self):

        self.whatsapp_interface.connect()
        self.connect_d.callback(None)

        self.stack.loop(discrete=0, count=1, timeout=1)

    def client_stop(self):
        self.transport.log.info("Stopping client ...")

        def _stop():
            self.transport.log.info("Sending disconnect ...")
            self.whatsapp_interface.disconnect()

        def _kill():
            raise WhatsAppClientDone("We are exiting NOW!")

        self.stack.execDetached(_stop)
        self.stack.execDetached(_kill)

    def send_to_stack(self, msg):
        def send():
            self.whatsapp_interface.send_to_human(msg)
        self.stack.execDetached(send)


class WhatsAppInterface(YowInterfaceLayer):

    def __init__(self, transport):
        super(WhatsAppInterface, self).__init__()
        self.transport = transport
        self.echo_to = self.transport.config.echo_to

    def send_to_human(self, msg):
        self.toLower(msg)

    @ProtocolEntityCallback("message")
    def onMessage(self, messageProtocolEntity):
        self.transport.log.info('Received %s' % (
            ' '.join(str(messageProtocolEntity).split('\n')),
        ))

        try:
            from_address = "+" + messageProtocolEntity.getFrom(False)
            from_address = from_address.decode("UTF-8")
            body = messageProtocolEntity.getBody().decode("UTF-8")
        except UnicodeDecodeError:
            message = ' '.join(str(messageProtocolEntity).split('\n'))
            self.transport.log.err('Cannot decode %r' % message)
            reactor.callFromThread(
                self.transport.handle_inbound_error,
                'Cannot decode', '%r' % message)
            return

        receipt = OutgoingReceiptProtocolEntity(
            messageProtocolEntity.getId(),
            msisdn_to_whatsapp(from_address).encode("UTF-8"),
            'read',
            messageProtocolEntity.getParticipant())
        self.toLower(receipt)

        if self.echo_to:
            self.transport.log.debug(
                'Echoing message received by transport to %s' % self.echo_to)
            reactor.callFromThread(
                self.transport.handle_outbound_message,
                TransportUserMessage(
                    to_addr=self.echo_to, from_addr=self.transport.our_msisdn,
                    content=body, transport_name='whatsapp',
                    transport_type='whatsapp'))

        reactor.callFromThread(
            self.transport.publish_message,
            from_addr=from_address, content=body,
            to_addr=self.transport.our_msisdn,
            transport_type=self.transport.transport_type,
            to_addr_type=TransportUserMessage.AT_MSISDN,
            from_addr_type=TransportUserMessage.AT_MSISDN)
        reactor.callFromThread(
            self.transport.handle_inbound_success)

    @ProtocolEntityCallback("receipt")
    def onReceipt(self, entity):
        '''receives confirmation of delivery to human'''
        self.transport.log.info(
            'Received %s' % ' '.join(str(entity).split('\n')))
        ack = OutgoingAckProtocolEntity(
            entity.getId(), "receipt", entity.getType(), entity.getFrom())
        self.toLower(ack)
        # if receipt means that it got delivered to the whatsapp user then
        # entity.getType() is None
        # if receipt means that the user has opened the message then
        # entity.getType() is 'read'
        # when it is delivered and read simultaneously,
        # only one receipt is sent and entity.getType() is 'read'
        reactor.callFromThread(
            self.transport._send_delivery_report, entity.getId())

    @ProtocolEntityCallback("ack")
    def onAck(self, ack):
        '''receives confirmation of delivery to server'''
        # sent_message_id: whatsapp id
        # user_message_id: vumi_id
        self.transport.log.info('Received %s' % ' '.join(str(ack).split('\n')))
        if ack.getClass() == "message":
            reactor.callFromThread(self.transport._send_ack, ack.getId())

    def onEvent(self, event):
        name = event.getName()
        if name == YowNetworkLayer.EVENT_STATE_CONNECTED:
            reactor.callFromThread(self.transport.handle_connected)
        elif name == YowNetworkLayer.EVENT_STATE_DISCONNECTED:
            reactor.callFromThread(
                self.transport.handle_disconnected, event.args.get('reason'))
