
const root = document.getElementById("app-root");

function loadView(path) {
  fetch(path).then(r => r.text()).then(html => {
    root.innerHTML = html;
  });
}

document.getElementById("nav-vetting").onclick = () => {
  root.innerHTML = "<h2>Vetting Dashboard</h2>";
};

document.getElementById("nav-profiles").onclick = () => {
  loadView("views/profiles.html");
};

document.getElementById("nav-jds").onclick = () => {
  loadView("views/jds.html");
};
