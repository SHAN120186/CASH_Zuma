import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.render_start import configure


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


if __name__=='__main__':unittest.main()
