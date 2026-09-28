"use strict";
const assert=require('node:assert/strict');
const {coincideConcepto,normalizarCatalogo}=require('../static/catalogo/catalogo.js');
const concept={conceptoBusqueda:'A-VECES glosa',knowledgeAreas:'Astronomía||Artes',semanticFields:JSON.stringify(['Expresiones de Tiempo','Clima']),hasVideo:'true',variationType:'both'};
const all={consulta:'',area:'',video:false,variacion:'',campos:[]};
assert(coincideConcepto(concept,all));
assert(coincideConcepto(concept,{consulta:'a veces',area:'astronomia',video:true,variacion:'both',campos:['clima']}));
for(const override of [{consulta:'inexistente'},{area:'biologia'},{variacion:'lexical'},{campos:['colores']}])assert(!coincideConcepto(concept,{...all,...override}));
assert(!coincideConcepto({...concept,hasVideo:'false'},{...all,video:true}));
assert(coincideConcepto(concept,{...all,campos:['colores','clima']}));
for(const variation of ['none','lexical','phonological','both']){
  assert(coincideConcepto({...concept,variationType:variation},{...all,variacion:variation}));
}
assert.equal(normalizarCatalogo('  MÁS/MENOS-QUÉ  '),'mas/menos que');
console.log('Filtros combinados: OK');

// Execute the shared DOM initialization with each collection's actual controls.
const vm=require('node:vm'),fs=require('node:fs');
const script=fs.readFileSync(require.resolve('../static/catalogo/catalogo.js'),'utf8');
function element(dataset={}){
  return {dataset,value:'',checked:false,children:[],listeners:{},attributes:{},
    classList:{toggle(){}},
    addEventListener(type,callback){this.listeners[type]=callback},
    append(...children){this.children.push(...children)},
    add(option){this.children.push(option)},
    getAttribute(name){return this.attributes[name]},
    setAttribute(name,value){this.attributes[name]=value},
    querySelectorAll(selector){
      const inputs=this.children.flatMap(row=>row.children||[]).filter(child=>child.type==='checkbox');
      return selector==='input:checked'?inputs.filter(input=>input.checked):selector==='input'?inputs:this.children;
    }};
}
for(const academic of [false,true]){
  const base=academic?'/colecciones/academica/v1':'/catalogo/v1';
  const card=element(concept),alternative=element({alternativeSelect:'1'});
  card.attributes.href=base+'/conceptos/1';alternative.attributes.href=base+'/alternativas/1';
  const controls=Object.fromEntries(['buscador-catalogo','filtro-video','filtro-variacion','contador-resultados','limpiar-filtros',
    ...(academic?['filtro-area']:['filtro-campos','buscador-campos'])].map(id=>['#'+id,element()]));
  const document={
    addEventListener(event,callback){callback()},
    querySelector(selector){return controls[selector]||null},
    querySelectorAll(selector){
      if(selector==='[data-concepto-busqueda]')return [card];
      if(selector==='[data-concepto-busqueda],[data-alternative-select]')return [card,alternative];
      if(selector==='[data-alternative-select]')return [alternative];
      return [];
    },
    createElement(){return element()}
  };
  vm.runInNewContext(script,{document,URL,URLSearchParams,
    Option:function(label,value){this.label=label;this.value=value},
    location:{search:'?q=a&area=astronomia&campo=clima&video=1&variacion=both',href:'https://example.test'+base}});
  for(const link of [card,alternative]){
    const url=new URL(link.attributes.href);
    assert(url.pathname.startsWith(base+'/'));
    assert.equal(url.searchParams.get('q'),'a');
    assert.equal(url.searchParams.get('video'),'1');
    assert.equal(url.searchParams.get('variacion'),'both');
    assert.equal(url.searchParams.has('area'),academic);
    assert.equal(url.searchParams.has('campo'),!academic);
  }
  assert.equal(controls['#contador-resultados'].textContent,'1 concepto(s)');
  controls['#buscador-catalogo'].value='inexistente';
  controls['#buscador-catalogo'].listeners.input();
  assert.equal(controls['#contador-resultados'].textContent,'0 concepto(s)');
}
console.log('DOM por colección, controles ausentes y navegación filtrada: OK');
