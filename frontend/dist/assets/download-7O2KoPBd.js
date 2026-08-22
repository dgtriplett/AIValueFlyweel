function n(c,t){const o=URL.createObjectURL(c),e=document.createElement("a");e.href=o,e.download=t,document.body.appendChild(e),e.click(),e.remove(),URL.revokeObjectURL(o)}export{n as s};
