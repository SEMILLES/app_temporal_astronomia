"use strict";
document.addEventListener("DOMContentLoaded",()=>{
  const buscador=document.querySelector("#buscador-catalogo");
  const elementos=[...document.querySelectorAll("[data-concepto-busqueda]")];
  const contador=document.querySelector("#contador-resultados");
  const normalizar=valor=>String(valor||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").replace(/[-_]+/g," ").replace(/\s+/g," ").trim().toLowerCase();
  const filtrar=()=>{const consulta=normalizar(buscador?.value);let visibles=0;elementos.forEach(elemento=>{const visible=!consulta||normalizar(elemento.dataset.conceptoBusqueda).includes(consulta);elemento.classList.toggle("oculto",!visible);if(visible)visibles+=1});if(contador)contador.textContent=`${visibles} concepto(s)`};
  buscador?.addEventListener("input",filtrar);filtrar();
  document.querySelectorAll("[data-pestana]").forEach(boton=>boton.addEventListener("click",()=>{const contenedor=boton.closest(".bloque");contenedor.querySelectorAll("[data-pestana]").forEach(item=>{const activo=item===boton;item.classList.toggle("activo",activo);item.setAttribute("aria-selected",String(activo))});contenedor.querySelectorAll("[data-panel]").forEach(panel=>panel.hidden=panel.dataset.panel!==boton.dataset.pestana)}));
});
