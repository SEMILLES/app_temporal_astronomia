"use strict";
const normalizarCatalogo=valor=>String(valor||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").replace(/[-_]+/g," ").replace(/\s+/g," ").trim().toLowerCase();
function coincideConcepto(datos,filtros){
  const normalizar=normalizarCatalogo,clasificaciones=JSON.parse(datos.classifications||"[]").map(normalizar);
  return (!filtros.consulta||normalizar(datos.conceptoBusqueda).includes(normalizar(filtros.consulta)))
    &&(!filtros.video||datos.hasVideo==="true")
    &&(!filtros.variacion||datos.variationType===filtros.variacion)
    &&(!filtros.clasificaciones.length||filtros.clasificaciones.some(valor=>clasificaciones.includes(valor)));
}
if(typeof module!=="undefined")module.exports={coincideConcepto,normalizarCatalogo};
if(typeof document!=="undefined")document.addEventListener("DOMContentLoaded",()=>{
  const buscador=document.querySelector("#buscador-catalogo"),filtroVideo=document.querySelector("#filtro-video"),filtroVariacion=document.querySelector("#filtro-variacion"),filtroClasificaciones=document.querySelector("#filtro-clasificaciones"),buscadorClasificaciones=document.querySelector("#buscador-clasificaciones");
  const conceptos=[...document.querySelectorAll("[data-concepto-busqueda]")],contador=document.querySelector("#contador-resultados"),normalizar=normalizarCatalogo;
  if(filtroClasificaciones){
    const campos=new Map();
    conceptos.forEach(elemento=>JSON.parse(elemento.dataset.classifications||"[]").forEach(campo=>campos.set(normalizar(campo),campo)));
    if(!campos.size){const aviso=document.createElement("p");aviso.textContent="No hay clasificaciones disponibles";filtroClasificaciones.append(aviso);}
    [...campos].sort((a,b)=>a[1].localeCompare(b[1],"es")).forEach(([value,label])=>{
      const fila=document.createElement("label"),casilla=document.createElement("input"),texto=document.createElement("span");
      casilla.type="checkbox";casilla.value=value;texto.textContent=label;
      fila.append(casilla,texto);filtroClasificaciones.append(fila);
    });
  }
  const parametro=filtroClasificaciones?.dataset.filterParam;
  const parametros=new URLSearchParams(location.search);
  if(buscador&&parametros.has("q"))buscador.value=parametros.get("q");
  if(filtroVideo)filtroVideo.checked=parametros.get("video")==="1";
  if(filtroVariacion)filtroVariacion.value=parametros.get("variacion")||"";
  filtroClasificaciones?.querySelectorAll("input").forEach(casilla=>casilla.checked=parametros.getAll(parametro).includes(casilla.value));
  const estado=()=>({consulta:buscador?.value||"",video:Boolean(filtroVideo?.checked),variacion:filtroVariacion?.value||"",clasificaciones:[...(filtroClasificaciones?.querySelectorAll("input:checked")||[])].map(casilla=>casilla.value)});
  const consultaActual=()=>{
    const filtros=estado(),params=new URLSearchParams();
    if(filtros.consulta)params.set("q",filtros.consulta);
    if(filtros.video)params.set("video","1");
    if(filtros.variacion)params.set("variacion",filtros.variacion);
    filtros.clasificaciones.forEach(valor=>params.append(parametro,valor));return params.toString();
  };
  const filtrar=()=>{
    const filtros=estado();let visibles=0;
    conceptos.forEach(elemento=>{const visible=coincideConcepto(elemento.dataset,filtros);elemento.classList.toggle("oculto",!visible);if(visible)visibles+=1});
    // Filter concepts only; every Alternative remains accessible.
    if(contador)contador.textContent=`${visibles} concepto(s)`;
    const query=consultaActual();
    document.querySelectorAll("[data-concepto-busqueda],[data-alternative-select]").forEach(enlace=>{const url=new URL(enlace.getAttribute("href"),location.href);url.search=query;enlace.setAttribute("href",url.href)});
  };
  const buscarCampos=()=>filtroClasificaciones?.querySelectorAll("label").forEach(fila=>fila.hidden=!normalizar(fila.textContent).includes(normalizar(buscadorClasificaciones?.value)));
  buscador?.addEventListener("input",filtrar);
  [filtroVideo,filtroVariacion,filtroClasificaciones].forEach(control=>control?.addEventListener("change",filtrar));
  buscadorClasificaciones?.addEventListener("input",buscarCampos);
  document.querySelector("#limpiar-filtros")?.addEventListener("click",()=>{
    if(buscador)buscador.value="";if(filtroVideo)filtroVideo.checked=false;if(filtroVariacion)filtroVariacion.value="";
    filtroClasificaciones?.querySelectorAll("input").forEach(casilla=>casilla.checked=false);
    if(buscadorClasificaciones)buscadorClasificaciones.value="";buscarCampos();filtrar();history.replaceState(null,"",location.pathname);
    // A direct server-side search may have returned a reduced concept list.
    if(parametros.has("q"))location.assign(location.pathname);
  });
  filtrar();
  const seleccionar=id=>{document.querySelectorAll("[data-alternative-select]").forEach(item=>item.classList.toggle("activo",item.dataset.alternativeSelect===id));document.querySelectorAll("[data-alternative-panel]").forEach(panel=>panel.hidden=panel.dataset.alternativePanel!==id);const enlace=document.querySelector(`.boton-variante[data-alternative-select="${CSS.escape(id)}"]`);if(enlace)history.replaceState(null,"",enlace.href);const panel=document.querySelector(`[data-alternative-panel="${CSS.escape(id)}"]`);if(panel){const reducir=window.matchMedia("(prefers-reduced-motion: reduce)").matches;panel.scrollIntoView({behavior:reducir?"auto":"smooth",block:"start"})}};
  document.querySelectorAll("[data-alternative-select]").forEach(item=>item.addEventListener("click",evento=>{evento.preventDefault();seleccionar(item.dataset.alternativeSelect)}));
  document.querySelectorAll("[data-pestana]").forEach(boton=>boton.addEventListener("click",()=>{const contenedor=boton.closest(".bloque");contenedor.querySelectorAll("[data-pestana]").forEach(item=>{const activo=item===boton;item.classList.toggle("activo",activo);item.setAttribute("aria-selected",String(activo))});contenedor.querySelectorAll("[data-panel]").forEach(panel=>panel.hidden=panel.dataset.panel!==boton.dataset.pestana)}));
});
