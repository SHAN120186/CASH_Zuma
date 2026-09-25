import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy.render_start import cloud_mode, configure


class RenderConfigurationTests(unittest.TestCase):
    def env(self):
        return {'DATABASE_URL':'postgres://test:private-test-only@db.example/neondb?sslmode=require&channel_binding=require',
                'JWT_SECRET':'x'*48,'RENDER_EXTERNAL_HOSTNAME':'uzgermed-example.onrender.com','PORT':'10000'}

    def test_external_postgres_normalized_and_secure_host_set(self):
        env=self.env();self.assertEqual(configure(env),10000)
        self.assertTrue(env['DATABASE_URL'].startswith('postgresql+psycopg://'))
        self.assertIn('channel_binding=require',env['DATABASE_URL'])
        self.assertEqual(env['PUBLIC_ORIGIN'],'https://uzgermed-example.onrender.com')
        self.assertEqual(env['COOKIE_SECURE'],'1');self.assertNotIn('*',env['ALLOWED_HOSTS'])

    def test_no_ephemeral_or_insecure_database_fallback(self):
        for value in ('','sqlite:///data.sqlite','postgresql://test:private-test-only@localhost/zuma?sslmode=require',
                      'postgresql://test:private-test-only@db.example/zuma','postgresql://test:private-test-only@db.example/zuma?sslmode=disable'):
            env=self.env();env['DATABASE_URL']=value
            with self.assertRaises(ValueError) as cm:configure(env)
            self.assertNotIn('private-test-only',str(cm.exception))

    def test_origin_secret_and_port_fail_closed(self):
        for key,value in [('JWT_SECRET','short'),('RENDER_EXTERNAL_HOSTNAME','*'),('PORT','0'),('PORT','not-a-port'),
                          ('PUBLIC_ORIGIN','http://uzgermed.example'),('PUBLIC_ORIGIN','https://user:pass@uzgermed.example'),
                          ('PUBLIC_ORIGIN','https://uzgermed.example/path'),('PUBLIC_ORIGIN','https://*.example')]:
            env=self.env();env[key]=value
            with self.assertRaises(ValueError):configure(env)

    def test_custom_https_domain_can_be_configured_after_registration(self):
        env=self.env();env['PUBLIC_ORIGIN']='https://cash.uzgermed.example'
        configure(env)
        self.assertIn('cash.uzgermed.example',env['ALLOWED_HOSTS'])
        self.assertIn('uzgermed-example.onrender.com',env['ALLOWED_HOSTS'])


class RenderDetectionTests(unittest.TestCase):
    """A service created by hand must not fall back to the local SQLite start."""

    def base(self):
        return {'DATABASE_URL':'postgres://test:private-test-only@db.example/neondb?sslmode=require',
                'JWT_SECRET':'x'*48,'RENDER_EXTERNAL_HOSTNAME':'uzgermed-example.onrender.com','PORT':'10000'}

    def test_cloud_mode_matches_platform_variables(self):
        self.assertTrue(cloud_mode({'UZGERMED_HOSTING':'render'}))
        self.assertTrue(cloud_mode({'RENDER':'true','RENDER_SERVICE_ID':'srv-abc123'}))
        self.assertFalse(cloud_mode({'RENDER':'true'}))
        self.assertFalse(cloud_mode({'RENDER_SERVICE_ID':'srv-abc123'}))
        self.assertFalse(cloud_mode({'UZGERMED_HOSTING':'local'}))
        self.assertFalse(cloud_mode({}))

    def entrypoint(self, env):
        """Run the real entrypoint with stub executables on a private PATH."""
        sh=shutil.which('sh')
        if not sh:self.skipTest('sh is not available on this machine')
        folder=ROOT/'.entrypoint-stub';folder.mkdir(exist_ok=True)
        try:
            for name,line in (('python','echo "python $*"'),('local-app','echo "local-app started"')):
                stub=folder/name;stub.write_text('#!/bin/sh\n'+line+'\n',encoding='ascii')
                stub.chmod(stub.stat().st_mode|stat.S_IEXEC|stat.S_IXGRP|stat.S_IXOTH)
            result=subprocess.run([sh,'deploy/entrypoint.sh','local-app'],cwd=ROOT,
                env={'PATH':'.entrypoint-stub','HOME':str(ROOT),**env},
                capture_output=True,text=True,timeout=120)
        finally:
            shutil.rmtree(folder,ignore_errors=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout

    def test_manual_service_without_switch_still_takes_the_cloud_path(self):
        out=self.entrypoint({'RENDER':'true','RENDER_SERVICE_ID':'srv-abc123'})
        self.assertIn('deploy/render_start.py',out)
        self.assertNotIn('manage.py',out)

    def test_declared_switch_takes_the_cloud_path(self):
        out=self.entrypoint({'UZGERMED_HOSTING':'render'})
        self.assertIn('deploy/render_start.py',out)

    def test_local_container_keeps_the_ordinary_start(self):
        out=self.entrypoint({})
        self.assertIn('manage.py init --no-user',out)
        self.assertIn('local-app started',out)
        self.assertNotIn('render_start',out)

    def test_unconfigured_cloud_service_stops_with_guidance_and_no_secrets(self):
        env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','SystemRoot','PATHEXT','TEMP','TMP','WINDIR')}
        env.update({'RENDER':'true','RENDER_SERVICE_ID':'srv-abc123',
                    'RENDER_EXTERNAL_HOSTNAME':'uzgermed-example.onrender.com','PYTHONIOENCODING':'utf-8'})
        result=subprocess.run([sys.executable,'deploy/render_start.py'],cwd=ROOT,env=env,
                              capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertIn('Render start aborted',result.stderr)
        self.assertIn('Do not create a second service',result.stderr)
        self.assertNotIn('private-test-only',result.stderr)

    def test_supplied_wildcard_host_and_insecure_cookie_are_replaced(self):
        env=self.base();env['ALLOWED_HOSTS']='*';env['COOKIE_SECURE']='0'
        configure(env)
        self.assertNotIn('*',env['ALLOWED_HOSTS'])
        self.assertEqual(env['ALLOWED_HOSTS'].split(',')[0],'uzgermed-example.onrender.com')
        self.assertEqual(env['COOKIE_SECURE'],'1')

    def test_assigned_render_port_is_used(self):
        env=self.base();env['PORT']='10000';self.assertEqual(configure(env),10000)
        env=self.base();env['PORT']='53017';self.assertEqual(configure(env),53017)
        env=self.base();env.pop('PORT');self.assertEqual(configure(env),10000)


if __name__=='__main__':unittest.main()
