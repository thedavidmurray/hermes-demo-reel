"""Hermes entrypoint. Import/register performs no disk, network or provider IO."""
from __future__ import annotations
import json
from .host import HostAdapter
from .interview import handle_demo
from .pipeline import render_job, deliver_job
from .schemas import ContractError, MediaPolicy
from .cli import setup_cli, run_cli


def register(ctx):
    def command(raw_args='', *, session_id='', source=None, platform='cli', **kwargs):
        if platform == 'cron' or kwargs.get('cron') or kwargs.get('interactive') is False or not session_id:
            return 'NEEDS_INPUT: run /demo interactively; no unattended approval or provider calls.'
        invocation = dict(session_id=session_id,source=source,platform=platform)
        adapter = HostAdapter(ctx,invocation=invocation,raw_args=raw_args)
        verb, _, job_id = raw_args.strip().partition(' ')
        if verb not in ('render','deliver'):
            return handle_demo(adapter,raw_args,session_id=session_id,source=source,platform=platform,**kwargs)
        try:
            owner = adapter.demo_owner(session_id=session_id)
            if verb == 'render':
                bundle = render_job(adapter,owner,job_id.strip(),policy=MediaPolicy(network_allowed=True))
                return ('READY: '+bundle.x_caption+'\nTo deliver video and frames into this same chat: '
                        '/demo deliver '+job_id.strip())
            receipt = deliver_job(adapter,owner,job_id.strip())
            return 'DELIVERED: media transport acknowledged message '+receipt.platform_message_id
        except ContractError as exc:
            return 'NEEDS_INPUT: '+str(exc)
        except Exception:
            # Never return credentials/provider errors; persistent claims remain uncertain.
            return 'BLOCKED_CAPABILITY: host operation failed or uncertain; do not retry paid render/send.'

    def reply_tool(args, **kwargs):
        # Tools are LLM-generated, not user testimony. No model field can authorize
        # reading a profile, a provider spend, or destination selection. Use slash
        # commands for v0; no fake trusted-user flag hidden in a JSON schema.
        return json.dumps({'code':'NEEDS_INPUT','text':
            'Use /demo with your answer. Source selection, generation consent and approval must be authentic user commands.'})

    ctx.register_command('demo',command,description='Interview, approve and render a session demo (≤90s)',args_hint='<answer | status | render JOB | deliver JOB>')
    ctx.register_tool('demo_reply','demo-reel',{'name':'demo_reply','description':
        'Explain how to continue the demo interview. Never supplies user consent or performs actions.',
        'parameters':{'type':'object','properties':{'answer':{'type':'string'}},'additionalProperties':False}},reply_tool)
    ctx.register_cli_command('demo-reel','Demo reel diagnostics and explicit operator CLI',setup_cli,
                             lambda args: run_cli(args,ctx=ctx))
