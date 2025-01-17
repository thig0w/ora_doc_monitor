from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
from dotenv import load_dotenv

import time

load_dotenv()

def main():
    # Get user/pass
    mos_user = os.getenv("MOSUSER")
    mos_pass = os.getenv("MOSPASS")

    if mos_user is None or mos_pass is None:
        print("Setar senha e pass!")
        return False
    
    # Inicializa o WebDriver
    driver = webdriver.Firefox()

    try:
        # Abre a página de login
        url = "https://support.oracle.com/epmos/faces/DocumentDisplay?id=1585843.1"
        driver.get(url)

        # Aguarda a página carregar
        wait = WebDriverWait(driver, 10)  # Ajuste o tempo se necessário

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

        # Aguarda a autenticação
        time.sleep(30)  # Ajuste conforme necessário

        print("Login realizado com sucesso!")

    except Exception as e:
        print(f"Ocorreu um erro: {e}")

    finally:
        # Fecha o navegador
        driver.quit()



if __name__ == "__main__":
    main()
