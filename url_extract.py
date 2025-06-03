import os
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from time import sleep

# Cabeçalhos para simular um navegador
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}


def download_pdfs(url, folder_name="mom_func_docs"):
    # Diretório para salvar os PDFs
    output_dir = os.path.join(os.getcwd(), folder_name)
    os.makedirs(output_dir, exist_ok=True)
    try:
        # Solicitação da página
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        # Parsear o conteúdo HTML
        soup = BeautifulSoup(response.text, "html.parser")

        # Encontrar todos os links com extensão .pdf
        pdf_links = soup.find_all("a", href=lambda href: href and href.endswith(".pdf"))

        print(f"Encontrados {len(pdf_links)} PDFs.")

        for link in pdf_links:
            pdf_url = link["href"]

            # Resolver URLs relativas
            if not pdf_url.startswith("http"):
                pdf_url = urljoin(url, pdf_url)

            # Nome do arquivo
            filename = os.path.join(output_dir, pdf_url.split("/")[-1])

            # Baixar o PDF
            print(f"Baixando: {pdf_url}")
            try:
                pdf_response = requests.get(pdf_url, headers=headers)
                pdf_response.raise_for_status()
            except Exception as e:
                print(f"Erro ao baixar o PDF: {e}")
                if pdf_links.count(link) < 5:
                    pdf_links.append(link)
                continue

            with open(filename, "wb") as pdf_file:
                pdf_file.write(pdf_response.content)

            print(f"Salvo: {filename}")
            # sleep to avoid connection to be blocked
            sleep(1)
    except Exception as e:
        print(f"Erro: {e}")


if __name__ == "__main__":
    # Executar o download
    download_pdfs(
        "https://docs.oracle.com/en/industries/retail/retail-merchandising-foundation-cloud/latest/books.html",
        "mfcs_docs",
    )
    download_pdfs(
        "https://docs.oracle.com/en/industries/retail/retail-pricing-cloud/latest/books.html",
        "rpm_docs",
    )
    download_pdfs(
        "https://docs.oracle.com/en/industries/retail/retail-invoice-matching-cloud/latest/books.html",
        "reim_docs",
    )
    download_pdfs(
        "https://docs.oracle.com/en/industries/retail/retail-integration-cloud/latest/books.html",
        "int_docs",
    )
    download_pdfs(
        "https://docs.oracle.com/en/industries/retail/retail-fiscal-management/latest/books.html",
        "rfm_docs",
    )
    download_pdfs(
        "https://docs.oracle.com/en/industries/retail/retail-allocation-cloud/latest/books.html",
        "alloc_docs",
    )
