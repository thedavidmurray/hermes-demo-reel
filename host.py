"""Trusted invocation adapter, not a model-supplied identity/consent field.

Only the /demo command receives source/session identity from Hermes. Tools cannot
attest consent. We never mutate PluginContext or core registries.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
try:
    from .schemas import Owner, NeedsInput, CapabilityUnavailable
except ImportError:
    from schemas import Owner, NeedsInput, CapabilityUnavailable

class HostAdapter:
    def __init__(self, ctx: Any, *, invocation: dict | None = None, raw_args: str = ''):
        self.ctx = ctx
        self.invocation = invocation or {}
        self.raw_args = raw_args

    @property
    def llm(self):
        return self.ctx.llm

    def demo_owner(self, *, session_id='', **kwargs):
        # invocation is captured ONLY in the register_command callback, never a tool field.
        if not self.invocation or kwargs.get('platform') == 'cron':
            raise NeedsInput('AUTHENTIC_COMMAND_REQUIRED')
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home()).resolve()
        source = self.invocation.get('source')
        platform = self.invocation.get('platform')
        sid = self.invocation.get('session_id')
        if not sid or sid != session_id:
            raise NeedsInput('LIVE_SESSION_REQUIRED')
        if source is None:
            if platform != 'cli':
                raise NeedsInput('AUTHENTIC_SOURCE_REQUIRED')
            return Owner(str(home),sid,'cli',sid,'local-user')
        from gateway.session import SessionSource
        if not isinstance(source, SessionSource) or not source.user_id or not source.chat_id:
            raise NeedsInput('AUTHENTIC_SOURCE_REQUIRED')
        if source.platform.value != platform:
            raise NeedsInput('SOURCE_PLATFORM_MISMATCH')
        # Threads must remain part of ownership and destination; not the parent room.
        thread = getattr(source,'thread_id',None)
        destination = str(source.chat_id) + (':'+str(thread) if thread else '')
        return Owner(str(home),sid,platform,destination,str(source.user_id))

    def demo_confirm(self, action, job, scope, raw_args, **kwargs):
        authentic = self.raw_args.strip()
        if authentic.startswith('for '):
            authentic = authentic.split(' ',2)[-1]
        if not self.invocation or raw_args != authentic:
            return False
        # Exact raw user command is required by interview at source, LLM and media gates.
        # No boolean/owner/hash accepted from the model-facing demo_reply tool.
        return action in ('source','generation','board')

    def get_tool_schemas(self):
        from tools.registry import registry
        return {e.name:e.schema for e in registry.get_all_entries()}

    @property
    def terminal_backend(self):
        # Read the installed terminal's actual scope-aware backend resolver. Read-only;
        # never infer locality from successful execution or a shared mounted nonce.
        from tools.terminal_tool import _session_scope
        return _session_scope().env_type

    def dispatch_tool(self, name, args):
        return self.ctx.dispatch_tool(name,args)

    def send_bundle(self, bundle, owner):
        try:
            from .delivery import format_delivery
        except ImportError:
            from delivery import format_delivery
        if owner != self.demo_owner(session_id=owner.session_id):
            raise NeedsInput('DESTINATION_OWNER_MISMATCH')
        if owner.platform not in ('discord','telegram'):
            # Desktop/CLI text-return MEDIA is not a playable transport receipt.
            raise CapabilityUnavailable('NATIVE_MEDIA_ACK_UNAVAILABLE_FOR_SURFACE')
        schemas = self.get_tool_schemas()
        schema = schemas.get('send_message',{})
        props = schema.get('parameters',schema.get('function',{}).get('parameters',{})).get('properties',{})
        if not {'target','message'} <= props.keys():
            raise CapabilityUnavailable('HOST_MEDIA_SEND_SCHEMA_UNAVAILABLE')
        raw = self.ctx.dispatch_tool('send_message',{'action':'send',
            'target':owner.platform+':'+owner.conversation_id,'message':format_delivery(bundle)})
        result = json.loads(raw) if isinstance(raw,str) else raw
        if (not isinstance(result,dict) or result.get('success') is not True or
            result.get('media_delivered') is not True or not result.get('message_id') or
            result.get('delivery_unconfirmed') or result.get('skipped') or result.get('warnings')):
            raise CapabilityUnavailable('MEDIA_DELIVERY_UNCONFIRMED_DO_NOT_RETRY')
        return str(result['message_id'])
