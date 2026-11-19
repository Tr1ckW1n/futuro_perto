// Mapa de Goiânia compartilhado pela página inicial e pela página do mapa.
function initCityMap(el,opts){
  const city=opts.city,points=opts.points||[],status=opts.status,say=t=>{if(status)status.textContent=t;};
  if(typeof L==='undefined'){say('Mapa indisponível sem a biblioteca externa. Consulte a lista de cursos.');return;}
  const map=L.map(el,{scrollWheelZoom:false,minZoom:10}).setView([-16.68,-49.26],11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'© OpenStreetMap contributors'}).addTo(map);
  const clean=s=>(s||'').normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().trim();
  // Agrupa os cursos do mesmo endereço em um único marcador.
  const markers=L.featureGroup().addTo(map);const groups={};
  for(const r of points){const key=r.lat.toFixed(5)+','+r.lon.toFixed(5);(groups[key]=groups[key]||[]).push(r);}
  for(const list of Object.values(groups)){const box=document.createElement('div');const strong=document.createElement('strong');strong.textContent=list[0].unit||list[0].institution;box.append(strong,document.createElement('br'),document.createTextNode(list.length+' curso(s) neste local'));const ul=document.createElement('ul');ul.style.cssText='margin:6px 0 0;padding-left:18px;max-height:180px;overflow:auto';for(const r of list){const li=document.createElement('li');const a=document.createElement('a');a.href='/oportunidade/'+r.id;a.textContent=r.title;li.append(a);ul.append(li);}box.append(ul);L.marker([list[0].lat,list[0].lon],{title:strong.textContent}).addTo(markers).bindPopup(box);}
  const fitMarkers=()=>{if(markers.getLayers().length)map.fitBounds(markers.getBounds(),{padding:[40,40],maxZoom:15});};
  fetch('https://cdn.jsdelivr.net/gh/henriquemalvar/br-geojson@main/dist/municipios/GO.geojson').then(r=>{if(!r.ok)throw Error('malha');return r.json()}).then(data=>{
    const feature=data.features.find(f=>clean(f.properties.nome||f.properties.NM_MUN)===clean(city));
    if(!feature)throw Error('municipio');
    const boundary=L.geoJSON(feature,{interactive:false,style:{color:'#067e72',weight:2,fillColor:'#c5f4e6',fillOpacity:.25}}).addTo(map);
    const bounds=boundary.getBounds();map.setMaxBounds(bounds.pad(0.3));
    if(opts.focusMarkers&&markers.getLayers().length)fitMarkers();else map.fitBounds(bounds,{padding:[12,12]});
    say(points.length?'Mapa carregado. Clique em um marcador para ver os cursos do local.':'Nenhum curso com os filtros atuais tem endereço no mapa. Veja a lista abaixo.');
  }).catch(()=>{say('Não foi possível carregar o contorno de '+city+'. A lista de cursos continua disponível.');fitMarkers();});
  return map;
}
