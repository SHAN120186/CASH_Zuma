import {defineAsyncComponent, defineComponent, h, shallowRef} from 'vue';
import {tx} from './i18n/index.js';

// Cache only downloaded component code. Props, permissions and financial data
// still belong to each mounted page and its existing company-scoped API reads.
export function lazyPage(loader, {reload} = {}) {
  return defineComponent({
    inheritAttrs: false,
    setup(_, {attrs, slots}) {
      const page = shallowRef();
      const loadingComponent = {render: () => h('p', {class: 'sub', role: 'status'}, tx('Загружаем раздел…'))};
      const errorComponent = {render: () => h('div', {class: 'error', role: 'alert'}, [
        h('p', tx('Не удалось загрузить раздел. Проверьте соединение и повторите.')),
        h('button', {type: 'button', class: 'secondary', onClick: retry}, tx('Повторить')),
        ...(typeof reload === 'function' ? [
          h('p', tx('Раздел мог обновиться. Сохраните текущую работу перед обновлением страницы.')),
          h('button', {type: 'button', class: 'secondary', onClick: reload}, tx('Обновить страницу')),
        ] : []),
      ])};
      function retry() {
        page.value = defineAsyncComponent({loader, delay: 120, timeout: 30000, loadingComponent, errorComponent});
      }
      retry();
      return () => h(page.value, attrs, slots);
    },
  });
}
