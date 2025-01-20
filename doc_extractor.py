from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.options import Options
import os
from dotenv import load_dotenv

import time

load_dotenv()


file_path = os.path.join(os.getcwd(), "func_docs")
os.makedirs(file_path, exist_ok=True)

# Função para esperar que todos os downloads sejam concluídos
def wait_for_downloads(directory, timeout=60, poll_interval=1):
    """
    Espera que todos os downloads na pasta sejam concluídos.
    
    :param directory: Caminho para o diretório de downloads.
    :param timeout: Tempo máximo de espera em segundos.
    :param poll_interval: Intervalo entre verificações em segundos.
    """
    end_time = time.time() + timeout
    while time.time() < end_time:
        # Verifica se existem arquivos temporários (exemplo: .crdownload ou .part)
        if not any(file.endswith(".crdownload") or file.endswith(".part") for file in os.listdir(directory)):
            return True
        time.sleep(poll_interval)
    raise TimeoutError("Os downloads não foram concluídos dentro do tempo especificado.")


# Wait until the page is fully loaded
def wait_for_page_load(driver, timeout=30):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def main():
    # Get user/pass
    mos_user = os.getenv("MOSUSER")
    mos_pass = os.getenv("MOSPASS")

    if mos_user is None or mos_pass is None:
        print("Setar senha e pass!")
        return False
    
    # Inicializa o WebDriver
    firefox_options = Options()
    firefox_options.set_preference('browser.download.folderList', 2)
    firefox_options.set_preference('browser.download.manager.showWhenStarting', False)
    firefox_options.set_preference('browser.download.dir', file_path)
    firefox_options.set_preference('browser.helperApps.neverAsk.saveToDisk', 'application/octet-stream,application/pdf')
    firefox_options.set_preference("pdfjs.disabled", True)

    driver = webdriver.Firefox(options=firefox_options)
    
    try:
        # Abre a página de login
        url = "https://support.oracle.com/epmos/faces/DocumentDisplay?id=1585843.1"
        driver.get(url)

        # Aguarda a página carregar
        wait = WebDriverWait(driver, 60)  # Ajuste o tempo se necessário

        # Localiza e preenche o campo de login
        #username_field = driver.find_element(By.ID, "idcs-signin-basic-signin-form-username")
        username_field = wait.until(EC.visibility_of_element_located((By.ID, "idcs-signin-basic-signin-form-username")))
        username_field.send_keys(mos_user)  # Substitua por seu nome de usuário
        username_field.send_keys(Keys.RETURN)

        # Localiza e preenche o campo de senha
        #password_field = driver.find_element(By.ID, "idcs-auth-pwd-input|input")
        password_field = wait.until(EC.visibility_of_element_located((By.ID, "idcs-auth-pwd-input|input")))
        password_field.send_keys(mos_pass)  # Substitua por sua senha

        # Envia o formulário de login
        password_field.send_keys(Keys.RETURN)

        # Wait page load
        print("Waiting first element to load...")
        wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "xq1")))
        print("Waiting for the page to fully load...")
        wait_for_page_load(driver)
        print("done...")

        elems = driver.find_elements(by=By.XPATH, value="//a[@href]")
        href_links = [e.get_attribute("href") for e in elems]

        for i in href_links:
            if i.__contains__('downloadattachmentprocessor'):
                print(i)
                driver.execute_script(f"window.open('{i}')")

        wait_for_downloads(file_path)

    except Exception as e:
        print(f"Ocorreu um erro: {e}")

    finally:
        # Fecha o navegador
        driver.quit()
        pass



if __name__ == "__main__":
    main()
