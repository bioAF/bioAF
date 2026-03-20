"use client";

import { useCallback, useState } from "react";

interface ExportPdfButtonProps {
  targetId: string;
  filename?: string;
}

export function ExportPdfButton({ targetId, filename = "report.pdf" }: ExportPdfButtonProps) {
  const [exporting, setExporting] = useState(false);

  const handleExport = useCallback(async () => {
    const el = document.getElementById(targetId);
    if (!el) return;

    setExporting(true);
    try {
      const html2canvas = (await import("html2canvas")).default;
      const { jsPDF } = await import("jspdf");

      const canvas = await html2canvas(el, {
        scale: 2,
        useCORS: true,
        logging: false,
        backgroundColor: "#ffffff",
      });

      const imgData = canvas.toDataURL("image/png");
      const imgWidth = canvas.width;
      const imgHeight = canvas.height;

      // A4 dimensions in points (72 dpi)
      const pageWidth = 595.28;
      const pageHeight = 841.89;
      const margin = 28;
      const contentWidth = pageWidth - margin * 2;
      const scaledHeight = (imgHeight * contentWidth) / imgWidth;

      const pdf = new jsPDF({
        orientation: scaledHeight > pageHeight ? "portrait" : "portrait",
        unit: "pt",
        format: "a4",
      });

      // If content fits one page, center it; otherwise paginate
      if (scaledHeight <= pageHeight - margin * 2) {
        pdf.addImage(imgData, "PNG", margin, margin, contentWidth, scaledHeight);
      } else {
        let yOffset = 0;
        const availableHeight = pageHeight - margin * 2;
        // How many pixels of the source image fit per page
        const srcPixelsPerPage = (availableHeight / contentWidth) * imgWidth;

        while (yOffset < imgHeight) {
          if (yOffset > 0) pdf.addPage();

          // Create a slice of the canvas for this page
          const sliceHeight = Math.min(srcPixelsPerPage, imgHeight - yOffset);
          const pageCanvas = document.createElement("canvas");
          pageCanvas.width = imgWidth;
          pageCanvas.height = sliceHeight;
          const ctx = pageCanvas.getContext("2d");
          if (ctx) {
            ctx.drawImage(canvas, 0, -yOffset);
            const pageData = pageCanvas.toDataURL("image/png");
            const renderedHeight = (sliceHeight * contentWidth) / imgWidth;
            pdf.addImage(pageData, "PNG", margin, margin, contentWidth, renderedHeight);
          }

          yOffset += srcPixelsPerPage;
        }
      }

      pdf.save(filename);
    } catch (e) {
      console.error("PDF export failed:", e);
    } finally {
      setExporting(false);
    }
  }, [targetId, filename]);

  return (
    <button
      onClick={handleExport}
      disabled={exporting}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
    >
      <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
      {exporting ? "Exporting..." : "Export PDF"}
    </button>
  );
}
