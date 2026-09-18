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
