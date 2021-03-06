"""
Support for Broadlink RM devices.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/switch.broadlink/
"""
import asyncio
from base64 import b64decode, b64encode
import binascii
from datetime import datetime, timedelta
import logging
import socket

import voluptuous as vol

from homeassistant.components.switch import (
    DOMAIN, PLATFORM_SCHEMA, SwitchDevice, ENTITY_ID_FORMAT)
from homeassistant.const import (
    CONF_COMMAND_OFF, CONF_COMMAND_ON, CONF_FRIENDLY_NAME, CONF_HOST, CONF_MAC,
    CONF_SWITCHES, CONF_TIMEOUT, CONF_TYPE, ATTR_ENTITY_ID, CONF_NAME)
import homeassistant.helpers.config_validation as cv
from homeassistant.util import Throttle, slugify
from homeassistant.util.dt import utcnow

REQUIREMENTS = ['broadlink==0.9.0']

_LOGGER = logging.getLogger(__name__)
#_LOGGER.setLevel(logging.DEBUG)

TIME_BETWEEN_UPDATES = timedelta(seconds=5)

DEFAULT_NAME = 'Broadlink switch'
DEFAULT_TIMEOUT = 10
DEFAULT_RETRY = 3
SERVICE_LEARN = 'broadlink_learn_command'
SERVICE_SEND = 'broadlink_send_packet'
CONF_SLOTS = 'slots'
CONF_COMMAND = 'command'
CONF_COMMAND_NAME = 'command_name'

SERVICE_LEARN_COMMAND = 'broadlink_rm_learn_command'
SERVICE_SEND_COMMAND = 'broadlink_rm_send_command'
DATA_KEY = 'switch.broadlink'
CONF_COMMANDS = 'commands'
IR_COMMANDS = {}

RM_TYPES = ['rm', 'rm2', 'rm_mini', 'rm_pro_phicomm', 'rm2_home_plus',
            'rm2_home_plus_gdt', 'rm2_pro_plus', 'rm2_pro_plus2',
            'rm2_pro_plus_bl', 'rm_mini_shate']
SP1_TYPES = ['sp1']
SP2_TYPES = ['sp2', 'honeywell_sp2', 'sp3', 'spmini2', 'spminiplus']
MP1_TYPES = ['mp1']
IR_TYPES = ['remote']

SWITCH_TYPES = RM_TYPES + SP1_TYPES + SP2_TYPES + MP1_TYPES + IR_TYPES

SWITCH_SCHEMA = vol.Schema({
    vol.Optional(CONF_COMMAND_OFF): cv.string,
    vol.Optional(CONF_COMMAND_ON): cv.string,
    vol.Optional(CONF_FRIENDLY_NAME): cv.string,
})

MP1_SWITCH_SLOT_SCHEMA = vol.Schema({
    vol.Optional('slot_1'): cv.string,
    vol.Optional('slot_2'): cv.string,
    vol.Optional('slot_3'): cv.string,
    vol.Optional('slot_4'): cv.string
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_SWITCHES, default={}):
        vol.Schema({cv.slug: SWITCH_SCHEMA}),
    vol.Optional(CONF_COMMANDS, default={}): vol.Schema({cv.slug: cv.string}),
    vol.Optional(CONF_SLOTS, default={}): MP1_SWITCH_SLOT_SCHEMA,
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_MAC): cv.string,
    vol.Optional(CONF_FRIENDLY_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_TYPE, default=SWITCH_TYPES[0]): vol.In(SWITCH_TYPES),
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    vol.Optional(CONF_NAME): cv.string,
})

SERVICE_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
})

SERVICE_SCHEMA_SEND_COMMAND = SERVICE_SCHEMA.extend({
    vol.Optional(CONF_COMMAND): cv.string,
    vol.Optional(CONF_COMMAND_NAME): cv.string
})

SERVICE_TO_METHOD = {
    SERVICE_LEARN_COMMAND: {'method': 'async_learn_command',
                            'schema': SERVICE_SCHEMA},
    SERVICE_SEND_COMMAND: {'method': 'async_send_packet',
                           'schema': SERVICE_SCHEMA_SEND_COMMAND},
}

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Broadlink switches."""
    import broadlink
    devices = config.get(CONF_SWITCHES)
    slots = config.get('slots', {})
    ip_addr = config.get(CONF_HOST)
    friendly_name = config.get(CONF_FRIENDLY_NAME)
    mac_addr = binascii.unhexlify(
        config.get(CONF_MAC).encode().replace(b':', b''))
    switch_type = config.get(CONF_TYPE)

    if DATA_KEY not in hass.data:
        hass.data[DATA_KEY] = {}
    commands = config.get(CONF_COMMANDS)
    IR_COMMANDS.update(commands)

    def _get_mp1_slot_name(switch_friendly_name, slot):
        """Get slot name."""
        if not slots['slot_{}'.format(slot)]:
            return '{} slot {}'.format(switch_friendly_name, slot)
        return slots['slot_{}'.format(slot)]

    if switch_type in IR_TYPES:
        broadlink_device = broadlink.rm((ip_addr, 80), mac_addr, None)
        broadlink_rm = BroadlinkRM(hass, config.get(CONF_NAME, 'broadlink_rm_' + ip_addr.replace('.', '_')), None, broadlink_device)
        hass.data[DATA_KEY][ip_addr] = broadlink_rm
        switches = [broadlink_rm]
    elif switch_type in RM_TYPES:
        broadlink_device = broadlink.rm((ip_addr, 80), mac_addr, None)
        # hass.services.register(DOMAIN, SERVICE_LEARN + '_' +
        #                        ip_addr.replace('.', '_'), _learn_command)
        # hass.services.register(DOMAIN, SERVICE_SEND + '_' +
        #                        ip_addr.replace('.', '_'), _send_packet,
        #                        vol.Schema({'packet': cv.ensure_list}))
        switches = []
        for object_id, device_config in devices.items():
            switches.append(
                BroadlinkRMSwitch(
                    object_id,
                    device_config.get(CONF_FRIENDLY_NAME, object_id),
                    broadlink_device,
                    device_config.get(CONF_COMMAND_ON),
                    device_config.get(CONF_COMMAND_OFF)
                )
            )
    elif switch_type in SP1_TYPES:
        broadlink_device = broadlink.sp1((ip_addr, 80), mac_addr, None)
        switches = [BroadlinkSP1Switch(friendly_name, broadlink_device)]
    elif switch_type in SP2_TYPES:
        broadlink_device = broadlink.sp2((ip_addr, 80), mac_addr, None)
        switches = [BroadlinkSP2Switch(friendly_name, broadlink_device)]
    elif switch_type in MP1_TYPES:
        switches = []
        broadlink_device = broadlink.mp1((ip_addr, 80), mac_addr, None)
        parent_device = BroadlinkMP1Switch(broadlink_device)
        for i in range(1, 5):
            slot = BroadlinkMP1Slot(
                _get_mp1_slot_name(friendly_name, i),
                broadlink_device, i, parent_device)
            switches.append(slot)

    broadlink_device.timeout = config.get(CONF_TIMEOUT)
    try:
        broadlink_device.auth()
    except socket.timeout:
        _LOGGER.error("Failed to connect to device")

    add_devices(switches)

    async def async_service_handler(service):
        """Map services to methods on Broadlink RM."""
        method = SERVICE_TO_METHOD.get(service.service)
        params = {key: value for key, value in service.data.items()
                  if key != ATTR_ENTITY_ID}
        entity_ids = service.data.get(ATTR_ENTITY_ID)
        if entity_ids:
            devices = [device for device in hass.data[DATA_KEY].values() if
                       device.entity_id in entity_ids]
        else:
            devices = hass.data[DATA_KEY].values()

        update_tasks = []
        for device in devices:
            if not hasattr(device, method['method']):
                continue
            await getattr(device, method['method'])(**params)
            update_tasks.append(device.async_update_ha_state(True))

        if update_tasks:
            await asyncio.wait(update_tasks, loop=hass.loop)
    
    for service in SERVICE_TO_METHOD:
        schema = SERVICE_TO_METHOD[service].get('schema', SERVICE_SCHEMA)
        hass.services.async_register(
            DOMAIN, service, async_service_handler, schema=schema)

class BroadlinkRM(SwitchDevice):
    """Representation of an Broadlink switch."""

    def __init__(self, hass, name, friendly_name, device):
        """Initialize the switch."""
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(name))
        self._name = friendly_name
        self._state = False
        self._device = device
        self.hass = hass

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return True

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._state

    def turn_on(self, **kwargs):
        """Turn the device on."""
        return

    def turn_off(self, **kwargs):
        """Turn the device off."""
        return

    @asyncio.coroutine
    def async_learn_command(self):
        """Handle a learn command."""
        try:
            auth = yield from self.hass.async_add_job(self._device.auth)
        except socket.timeout:
            _LOGGER.error("Failed to connect to device, timeout")
            return
        if not auth:
            _LOGGER.error("Failed to connect to device")
            return

        yield from self.hass.async_add_job(self._device.enter_learning)

        _LOGGER.info("Press the key you want Home Assistant to learn")
        start_time = utcnow()
        while (utcnow() - start_time) < timedelta(seconds=20):
            packet = yield from self.hass.async_add_job(
                self._device.check_data)
            if packet:
                log_msg = "Received packet is: {}".\
                          format(b64encode(packet).decode('utf8'))
                _LOGGER.info(log_msg)
                self.hass.components.persistent_notification.async_create(
                    log_msg, title='Broadlink switch')
                return
            yield from asyncio.sleep(1, loop=self.hass.loop)
        _LOGGER.error("Did not received any signal")
        self.hass.components.persistent_notification.async_create(
            "Did not received any signal", title='Broadlink switch')

    @asyncio.coroutine
    def async_send_packet(self, command = None, command_name = None):
        """Send a packet."""
        if command_name is not None:
            command = IR_COMMANDS.get(command_name)
            _LOGGER.debug('IR command:%s',command)
        if command is None:
            _LOGGER.error('No IR command.')
            return
        for retry in range(DEFAULT_RETRY):
            try:
                extra = len(command) % 4
                if extra > 0:
                    command = command + ('=' * (4 - extra))
                payload = b64decode(command)
                yield from self.hass.async_add_job(
                    self._device.send_data, payload)
                break
            except (socket.timeout, ValueError):
                try:
                    yield from self.hass.async_add_job(
                        self._device.auth)
                except socket.timeout:
                    if retry == DEFAULT_RETRY-1:
                        _LOGGER.error("Failed to send packet to device")

class BroadlinkRMSwitch(SwitchDevice):
    """Representation of an Broadlink switch."""

    def __init__(self, name, friendly_name, device, command_on, command_off):
        """Initialize the switch."""
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(name))
        self._name = friendly_name
        self._state = False
        self._command_on = b64decode(command_on) if command_on else None
        self._command_off = b64decode(command_off) if command_off else None
        self._device = device

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return True

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._state

    def turn_on(self, **kwargs):
        """Turn the device on."""
        if self._sendpacket(self._command_on):
            self._state = True
            self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn the device off."""
        if self._sendpacket(self._command_off):
            self._state = False
            self.schedule_update_ha_state()

    def _sendpacket(self, packet, retry=2):
        """Send packet to device."""
        if packet is None:
            _LOGGER.debug("Empty packet")
            return True
        try:
            self._device.send_data(packet)
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error(error)
                return False
            if not self._auth():
                return False
            return self._sendpacket(packet, retry-1)
        return True

    def _auth(self, retry=2):
        try:
            auth = self._device.auth()
        except socket.timeout:
            auth = False
        if not auth and retry > 0:
            return self._auth(retry-1)
        return auth


class BroadlinkSP1Switch(BroadlinkRMSwitch):
    """Representation of an Broadlink switch."""

    def __init__(self, friendly_name, device):
        """Initialize the switch."""
        super().__init__(friendly_name, friendly_name, device, None, None)
        self._command_on = 1
        self._command_off = 0

    def _sendpacket(self, packet, retry=2):
        """Send packet to device."""
        try:
            self._device.set_power(packet)
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error(error)
                return False
            if not self._auth():
                return False
            return self._sendpacket(packet, retry-1)
        return True


class BroadlinkSP2Switch(BroadlinkSP1Switch):
    """Representation of an Broadlink switch."""

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return False

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    def update(self):
        """Synchronize state with switch."""
        self._update()

    def _update(self, retry=2):
        """Update the state of the device."""
        try:
            state = self._device.check_power()
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error(error)
                return
            if not self._auth():
                return
            return self._update(retry-1)
        if state is None and retry > 0:
            return self._update(retry-1)
        self._state = state


class BroadlinkMP1Slot(BroadlinkRMSwitch):
    """Representation of a slot of Broadlink switch."""

    def __init__(self, friendly_name, device, slot, parent_device):
        """Initialize the slot of switch."""
        super().__init__(friendly_name, friendly_name, device, None, None)
        self._command_on = 1
        self._command_off = 0
        self._slot = slot
        self._parent_device = parent_device
        self._update_force = True  # force update()

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return False

    @property
    def available(self) -> bool:
        """Return true if power strip is available."""
        return self._parent_device.available

    def _sendpacket(self, packet, retry=2):
        """Send packet to device."""
        try:
            self._device.set_power(self._slot, packet)
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error(error)
                return False
            if not self._auth():
                return False
            return self._sendpacket(packet, max(0, retry-1))
        return True

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    def turn_on(self, **kwargs):
        """Turn the device on."""
        if self._sendpacket(self._command_on):
            self._state = True
            self._update_force = True
            self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn the device off."""
        if self._sendpacket(self._command_off):
            self._state = False
            self._update_force = True
            self.schedule_update_ha_state()

    def update(self):
        """Trigger update for all switches on the parent device."""
        # in HA's auto update task, only update_slot call update()
        # TIME_BETWEEN_UPDATES works with a small SCAN_INTERVAL
        if (not self._update_force and
            (datetime.now() - self._parent_device.last_update_time <
             TIME_BETWEEN_UPDATES or
             self._parent_device._update_slot != self._slot)):
            pass
        else:
            self._parent_device.update()
        self._state = self._parent_device.get_outlet_status(self._slot)
        self._update_force = False


class BroadlinkMP1Switch:
    """Representation of a Broadlink switch - To fetch states of all slots."""

    def __init__(self, device):
        """Initialize the switch."""
        self._device = device
        self._states = None
        self._available = False
        self._last_update_time = datetime.now()
        self._update_slot = 1

    @property
    def available(self) -> bool:
        """Return true if power strip is available."""
        return self._available

    @property
    def last_update_time(self):
        return self._last_update_time

    def get_outlet_status(self, slot):
        """Get status of outlet from cached status list."""
        if self._states is None:
            return None
        return self._states['s{}'.format(slot)]

    def update(self):
        """Fetch new state data for this device."""
        self._update()

    def _update(self, retry=2):
        """Update the state of the device."""
        self._last_update_time = datetime.now()
        try:
            states = self._device.check_power()
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                if self._available:  # announce once
                    _LOGGER.error("Unable to update power strip status, \
                    error: %s", error)
                self._available = False
                return
            if not self._auth():
                if self._available:  # announce once
                    _LOGGER.error("Unable to update power strip status, \
                    error: auth failure")
                self._available = False
                return
            return self._update(max(0, retry-1))
        if states is None and retry > 0:
            return self._update(max(0, retry-1))
        self._states = states
        self._available = True

    def _auth(self, retry=2):
        """Authenticate the device."""
        try:
            auth = self._device.auth()
        except socket.timeout:
            auth = False
        if not auth and retry > 0:
            return self._auth(retry-1)
        return auth
