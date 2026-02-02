
function navigate(view){
 fetch('/ui/views/'+view+'.html')
  .then(r=>r.text())
  .then(h=>document.getElementById('app-view').innerHTML=h);
}
window.onload=()=>navigate('vetting');
