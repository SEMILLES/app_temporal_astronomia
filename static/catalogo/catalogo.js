"use strict";
document.addEventListener("DOMContentLoaded",()=>{
  const buscador=document.querySelector("#buscador-catalogo");
  const filtroVideo=document.querySelector("#filtro-video");
  const filtroArea=document.querySelector("#filtro-area");
  const conceptos=[...document.querySelectorAll("[data-concepto-busqueda]")];
  const contador=document.querySelector("#contador-resultados");
  const normalizar=valor=>String(valor||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").replace(/[-_]+/g," ").replace(/\s+/g," ").trim().toLowerCase();
  if(filtroArea){const areas=new Map();conceptos.forEach(elemento=>(elemento.dataset.knowledgeAreas||"").split("||").filter(Boolean).forEach(area=>areas.set(normalizar(area),area)));[...areas].sort((a,b)=>a[1].localeCompare(b[1],"es")).forEach(([value,label])=>filtroArea.add(new Option(label,value)))}
  const filtrar=()=>{const consulta=normalizar(buscador?.value),area=filtroArea?.value||"",soloVideo=Boolean(filtroVideo?.checked);let visibles=0;conceptos.forEach(elemento=>{const areas=(elemento.dataset.knowledgeAreas||"").split("||").map(normalizar);const visible=(!consulta||normalizar(elemento.dataset.conceptoBusqueda).includes(consulta))&&(!area||areas.includes(area))&&(!soloVideo||elemento.dataset.hasVideo==="true");elemento.classList.toggle("oculto",!visible);if(visible)visibles+=1});document.querySelectorAll(".selector-alternativas [data-has-video]").forEach(elemento=>elemento.classList.toggle("oculto",soloVideo&&elemento.dataset.hasVideo!=="true"));document.querySelectorAll("[data-variation-group]").forEach(grupo=>grupo.classList.toggle("oculto",soloVideo&&![...grupo.querySelectorAll("[data-has-video]")].some(item=>item.dataset.hasVideo==="true")));if(contador)contador.textContent=`${visibles} concepto(s)`};
  buscador?.addEventListener("input",filtrar);filtroArea?.addEventListener("change",filtrar);filtroVideo?.addEventListener("change",filtrar);filtrar();
  const seleccionar=id=>{document.querySelectorAll("[data-alternative-select]").forEach(item=>item.classList.toggle("activo",item.dataset.alternativeSelect===id));document.querySelectorAll("[data-alternative-panel]").forEach(panel=>panel.hidden=panel.dataset.alternativePanel!==id);const enlace=document.querySelector(`.boton-variante[data-alternative-select="${CSS.escape(id)}"]`);if(enlace)history.replaceState(null,"",enlace.href)};
  document.querySelectorAll("[data-alternative-select]").forEach(item=>item.addEventListener("click",evento=>{evento.preventDefault();seleccionar(item.dataset.alternativeSelect)}));
  document.querySelectorAll("[data-pestana]").forEach(boton=>boton.addEventListener("click",()=>{const contenedor=boton.closest(".bloque");contenedor.querySelectorAll("[data-pestana]").forEach(item=>{const activo=item===boton;item.classList.toggle("activo",activo);item.setAttribute("aria-selected",String(activo))});contenedor.querySelectorAll("[data-panel]").forEach(panel=>panel.hidden=panel.dataset.panel!==boton.dataset.pestana)}));
});
