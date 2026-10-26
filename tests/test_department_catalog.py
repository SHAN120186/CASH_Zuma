import unittest
import tests.test_site as base

class DepartmentCatalogTests(base.SiteTests):
    def test_defaults_company_isolation_and_duplicate(self):
        initial=self.client.get('/api/departments',headers=self.h).json()
        self.assertEqual(len(initial['items']),7)
        body={'version':0,'name_ru':'Synthetic custom','name_uz':'Synthetic maxsus','active':True}
        response=self.post('/api/departments',body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['version'],1)
        self.assertEqual(self.post('/api/departments',body).status_code,409)
        body['version']=1
        self.assertEqual(self.post('/api/departments',body).status_code,409)
        other_id=next(c['id'] for c in self.client.get('/api/bootstrap').json()['companies'] if c['id']!=initial['company_id'])
        other=self.client.get('/api/departments',headers={**self.h,'X-Company-ID':str(other_id)}).json()
        self.assertEqual(len(other['items']),7)
    def test_custom_department_request_and_disabled_history(self):
        body={'version':0,'name_ru':'Synthetic department','name_uz':'Synthetic bolim','active':True}
        result=self.post('/api/departments',body).json();code=result['items'][-1]['code']
        request_body=self.request_body('100');request_body['department']=code
        response=self.post('/api/requests',request_body)
        self.assertEqual(response.status_code,200,response.text)
        request=response.json();self.assertEqual(request['department_name_uz'],'Synthetic bolim')
        body.update(version=1,active=False,name_ru='Renamed department')
        response=self.client.put('/api/departments/'+code,headers=self.h,json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.post('/api/requests',request_body).status_code,422)
        request_body.update(version=request['version'],reason='Preserve historical department')
        response=self.client.put('/api/requests/'+str(request['id']),headers=self.h,json=request_body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['department_label'],'Renamed department')
        body.update(version=2,active=True)
        self.assertEqual(self.client.put('/api/departments/'+code,headers=self.h,json=body).status_code,200)
        request_body.pop('version');request_body.pop('reason')
        self.assertEqual(self.post('/api/requests',request_body).status_code,200)
    def test_hidden_catalog_denies_read_and_view_denies_write(self):
        from sqlalchemy import select
        from app.db import unit,User
        client,headers=self.make_user('director','department_hidden')
        with unit() as session:uid=session.scalar(select(User.id).where(User.username=='department_hidden'))
        try:
            for version,mode in enumerate(('hidden','view')):
                result=self.client.put(f'/api/admin/users/{uid}/sections',headers=self.h,json={'company_id':self.company,'version':version,'sections':{'categories':mode},'reason':'Synthetic catalog access check'})
                self.assertEqual(result.status_code,200,result.text)
                login=client.post('/api/login',json={'username':'department_hidden','password':base.PASSWORD})
                self.assertEqual(login.status_code,200,login.text)
                scoped={'X-CSRF-Token':login.json()['csrf'],'X-Company-ID':str(self.company)}
                response=client.get('/api/departments',headers=scoped)
                self.assertEqual(response.status_code,403 if mode=='hidden' else 200,response.text)
                self.assertEqual(client.post('/api/departments',headers=scoped,json={'version':0,'name_ru':'Blocked','name_uz':'Blocked'}).status_code,403)
        finally:client.close()
    def test_nonadmin_cannot_write_catalog(self):
        client,headers=self.make_user('director','department_director')
        try:
            response=client.post('/api/departments',headers=headers,json={'version':0,'name_ru':'Forbidden','name_uz':'Forbidden','active':True})
            self.assertEqual(response.status_code,403,response.text)
        finally:client.close()

def tearDownModule():
    base.engine.dispose()

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(DepartmentCatalogTests(name) for name in DepartmentCatalogTests.__dict__ if name.startswith('test_'))
if __name__=='__main__':unittest.main()
