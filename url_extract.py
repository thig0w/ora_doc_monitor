import os
import requests
from bs4 import BeautifulSoup

# URL da página com os PDFs
url = "https://docs.oracle.com/en/industries/retail/retail-merchandising-foundation-cloud/latest/books.html"

# Cabeçalhos para simular um navegador
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# Diretório para salvar os PDFs
output_dir = os.path.join(os.getcwd(), "mom_func_docs")
os.makedirs(output_dir, exist_ok=True)

def download_pdfs(url):
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
                pdf_url = requests.compat.urljoin(url, pdf_url)
            
            # Nome do arquivo
            filename = os.path.join(output_dir, pdf_url.split("/")[-1])
            
            # Baixar o PDF
            print(f"Baixando: {pdf_url}")
            pdf_response = requests.get(pdf_url, headers=headers)
            pdf_response.raise_for_status()
            
            with open(filename, "wb") as pdf_file:
                pdf_file.write(pdf_response.content)
            
            print(f"Salvo: {filename}")
    except Exception as e:
        print(f"Erro: {e}")

# Executar o download
download_pdfs(url)