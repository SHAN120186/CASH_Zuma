import {defineConfig} from 'vite';
import vue from '@vitejs/plugin-vue';
export default defineConfig({plugins:[vue()],base:'/static/erp/',build:{outDir:'../app/static/erp',emptyOutDir:true},server:{proxy:{'/api':'http://127.0.0.1:8001'}}});
