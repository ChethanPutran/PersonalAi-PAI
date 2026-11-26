import asyncio
import unittest
from types import SimpleNamespace
from pai.security.manager import build_authorization_manager
from pai.security.authorization.context import AuthorizationContext
from pai.common.enums.permission import Origin, PermissionAction as A, PermissionResource as R, PrincipalType as P
from pai.security.authorization.request import Actor, AuthorizationRequest, ResourceRef

class SecurityTests(unittest.TestCase):
    def setUp(self):
        # The pipeline under test (SecurityManager only adds DB/plugin lookups around it).
        self.security = SimpleNamespace(authorization=build_authorization_manager())
    def authz(self, request):
        """Run the (async) pipeline synchronously; the caller supplies only the request."""
        return asyncio.run(self.security.authorization.authorize(request, AuthorizationContext(request=request)))
    def req(self, rtype=R.FILESYSTEM, action=A.READ, rid=None, **kwargs):
        return AuthorizationRequest(
            subject=Actor(P.PLUGIN, 'test-plugin'),
            on_behalf_of=(Actor(P.USER, 'test'),),
            action=action, resource=ResourceRef(rtype, rid), **kwargs)
    def test_granted_filesystem_read(self):
        self.security.authorization.consent_manager.grant('user:test','test-plugin','filesystem.read')
        d=self.authz(self.req(rid='/workspace/a',parameters={'path':'/workspace/a'},context={'filesystem_roots':['/workspace']}))
        self.assertTrue(d.allowed)
        self.assertIsNotNone(d.token)
    def test_missing_consent_requires_approval(self):
        d=self.authz(self.req(rid='/workspace/a',parameters={'path':'/workspace/a'},context={'filesystem_roots':['/workspace']}))
        self.assertEqual(d.effect.value,'approval_required')
    def test_out_of_scope_path_denied(self):
        self.security.authorization.consent_manager.grant('user:test','test-plugin','filesystem.read')
        d=self.authz(self.req(rid='/etc/passwd',parameters={'path':'/etc/passwd'},context={'filesystem_roots':['/workspace']}))
        self.assertEqual(d.effect.value,'deny')
    def test_critical_action_denied(self):
        d=self.authz(self.req(rtype=R.SYSTEM,action=A.EXECUTE))
        self.assertEqual(d.effect.value,'deny')

    def test_shell_execute_asks_every_time_even_with_a_standing_consent(self):
        # Not denied any more, but a permission-level consent can never make it silent: only the
        # confirmation gate (once, or a grant bound to the exact command) resolves it.
        self.security.authorization.consent_manager.grant('user:test','test-plugin','shell.execute')
        d=self.authz(self.req(rtype=R.SHELL,action=A.EXECUTE))
        self.assertEqual(d.effect.value,'approval_required')
        self.assertIn('risk-policy', d.policy_ids)

    def test_camera_control_requires_approval_even_with_consent(self):
        self.security.authorization.consent_manager.grant('user:test','test-plugin','camera.control')
        d=self.authz(self.req(rtype=R.CAMERA,action=A.CONTROL,rid='front',target_device_id='d1',context={'allowed_devices':['front']}))
        self.assertEqual(d.effect.value,'approval_required')

    def test_unregistered_permission_fails_closed(self):
        d=self.authz(self.req(rtype=R.CONTACTS,action=A.READ,rid='x'))
        self.assertEqual(d.effect.value,'deny')

    def test_request_is_immutable(self):
        r=self.req(rid='/workspace/a',parameters={'path':'/workspace/a'})
        with self.assertRaises(TypeError):
            r.parameters['path']='/etc/passwd'

    def test_identity_chain_and_derived_fields(self):
        r=AuthorizationRequest(subject=Actor(P.PLUGIN,'p'),
            on_behalf_of=(Actor(P.USER,'u'),Actor(P.AGENT,'a'),Actor(P.TASK,'t')),
            action=A.READ,resource=ResourceRef(R.FILESYSTEM,'/x'))
        self.assertEqual((r.user_id,r.plugin_id,r.agent_id,r.task_id),('u','p','a','t'))
        self.assertEqual(r.permission,'filesystem.read')
        self.assertEqual(r.principal_key,'user:u')
        self.assertEqual(r.origin,Origin.AGENT_AUTONOMOUS)  # most restrictive default
        self.assertIsNotNone(r.requested_at.tzinfo)

    def test_validation_rejects_bad_input(self):
        with self.assertRaises(ValueError): Actor(P.USER,'')
        with self.assertRaises(ValueError): ResourceRef(R.FILESYSTEM,' ')
        with self.assertRaises(ValueError): self.req(action='teleport')

    def test_resource_parameter_mismatch_denied(self):
        self.security.authorization.consent_manager.grant('user:test','test-plugin','filesystem.read')
        d=self.authz(self.req(rid='/workspace/a',parameters={'path':'/workspace/../etc/passwd'},context={'filesystem_roots':['/workspace']}))
        self.assertEqual(d.effect.value,'deny')

    def test_audit_dict_redacts_sensitive_values(self):
        r=self.req(rid='/workspace/a',parameters={'path':'/workspace/a','api_key':'sk-1','nested':{'Password':'x','ok':1},'body':'hi'},trace_id='t1')
        a=r.to_audit_dict()
        self.assertEqual(a['parameters']['path'],'/workspace/a')
        self.assertEqual(a['parameters']['api_key'],'[REDACTED]')
        self.assertEqual(a['parameters']['nested'],{'Password':'[REDACTED]','ok':1})
        self.assertEqual(a['parameters']['body'],'[REDACTED]')
        self.assertEqual((a['subject'],a['on_behalf_of'],a['trace_id']),('plugin:test-plugin',['user:test'],'t1'))
        import json; json.dumps(a)

    def test_decision_is_audited_without_secrets(self):
        self.security.authorization.consent_manager.grant('user:test','test-plugin','filesystem.read')
        self.authz(self.req(rid='/workspace/a',parameters={'path':'/workspace/a','token':'sekret'},context={'filesystem_roots':['/workspace']}))
        ev=self.security.authorization.audit_logger.repository.events[-1]
        self.assertNotIn('sekret',str(ev.metadata))

if __name__ == '__main__':
    unittest.main()