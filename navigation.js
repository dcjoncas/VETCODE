
// v3.0.1 Navigation
const VIEWS={VETTING:'vetting',PROFILES:'profiles',JDS:'jds'};
function setView(v){
 document.getElementById('view-vetting').style.display=v==='vetting'?'block':'none';
 document.getElementById('view-profiles').style.display=v==='profiles'?'block':'none';
 document.getElementById('view-jds').style.display=v==='jds'?'block':'none';
}
