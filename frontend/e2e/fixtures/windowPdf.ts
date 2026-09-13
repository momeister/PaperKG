/** Several pages, with real text/ink on every page, shared by Chromium and WebKit probes. */
export function windowPdf() {
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', ''];
  objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  const kids = [];
  for (let page = 1; page <= 6; page++) {
    const id = objects.length + 1;
    kids.push(`${id} 0 R`);
    const stream = `BT /F1 14 Tf 30 540 Td (Window PDF search selection - page ${page}) Tj 0 -200 Td (Reading position ${page}) Tj ET`;
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 600] /Resources << /Font << /F1 3 0 R >> >> /Contents ${id + 1} 0 R >>`, `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  }
  objects[1] = `<< /Type /Pages /Kids [${kids.join(' ')}] /Count 6 >>`;
  let pdf = '%PDF-1.4\n';
  const offsets = objects.map((o, i) => { const at = pdf.length; pdf += `${i + 1} 0 obj\n${o}\nendobj\n`; return at; });
  const xref = pdf.length;
  return pdf + `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.map(o => `${String(o).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Root 1 0 R /Size ${objects.length + 1} >>\nstartxref\n${xref}\n%%EOF\n`;
}
