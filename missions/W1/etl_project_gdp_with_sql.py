import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import os
import time
import requests
from bs4 import BeautifulSoup
import json
from io import StringIO
import yaml

# 설정 파일에서 데이터 로드
with open('path_url_settings.yaml', 'r') as file:
    settings = yaml.safe_load(file)

log_file = settings['file_name']['log_file']
region_data_json_file = settings['file_name']['region_data_json_file']
GDP_json_file = settings['file_name']['GDP_json_file']
db_file = settings['file_name']['db_file']
GDP_WIKIPEDIA_URL = settings['URL']['GDP_WIKIPEDIA_URL']

# 로그 기록 함수
def log(message): # log message를 etl_project_log.txt 파일에 기록
    with open(log_file, 'a') as f:
        current_time = datetime.now().strftime('%Y-%b-%d %H:%M:%S')
        f.write(f"{current_time}, {message}\n") # 현재 날짜 및 시간, message를 write

def extract_data(url): # url에서 Data를 extraction
    log("Data extraction started") # Extract log 기록
    
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # 데이터프레임 리스트로 가져오기
    tables = pd.read_html(StringIO(str(soup)), match='Forecast')
    df = tables[0][['Country/Territory', 'IMF[1][13]']] # Table을 DataFrame에 담는다.
    
    df.columns = df.columns.droplevel() # 2중 column droplevel
    # 숫자가 아닌 값은 제외
    df['Forecast'] = pd.to_numeric(df['Forecast'], errors='coerce')
    df = df.dropna(subset=['Forecast'])
    
    # column name 변경후 Billion 단위로 수정
    df = df.rename(columns={'Country/Territory': 'Country', 'Forecast': 'GDP_USD_billion', 'Year': 'Extracion_Date'})
    df['GDP_USD_billion'] = (df['GDP_USD_billion'] / 1000).round(2)
    
    log("Data extraction completed")
    return df

def transform_data(df): # Data 전처리 및 변환
    log("Data transformation started") # Transform log 기록
    
    df = ( # 필요없는 행 제외, GDP가 높은 순서로 sorting
        df.query('Country != "World"')
        .sort_values('GDP_USD_billion', ascending=False)
        .reset_index(drop=True)
    )

    # 현재 날짜와 시간 추가
    df['Extracion_Date'] = datetime.now().strftime("%Y-%b-%d %H:%M")
    
    # Region 정보를 file에서 읽어와서 Region column 추가
    with open(region_data_json_file) as Country_Region:
        Country_Region = json.load(Country_Region)
        country_to_continent = {country: continent for continent, countries in Country_Region.items() for country in countries}
        df['Region'] = df['Country'].map(country_to_continent).fillna('Other')
    
    log("Data transformation completed")
    return df

def load_data(df, db_path): # Data를 DB에 Load
    log("Data load started") # Load log 기록
    
    df.to_json(GDP_json_file, orient='records', lines=True)
    
    # SQLite에 연결
    conn = sqlite3.connect(db_path)
    # 전체 데이터(Total_Countries_by_GDP) 저장
    df.to_sql('Total_Countries_by_GDP', conn, if_exists='append', index=False)
    
    conn.commit()
    conn.close()
    log("Data load completed")

# query 실행 함수
def get_sql(query, db_name=db_file):
    try:
        with sqlite3.connect(db_name) as conn:
            cur = conn.cursor()
            cur.execute(query)
            return cur.fetchall()
    except Exception as e:
        print(e)

# 100 Billion USD 이상의 Country SELECT
def get_OVER_100b():
    query = """
        SELECT * 
        FROM Total_Countries_by_GDP
        WHERE GDP_USD_billion >= 100
        """
    return get_sql(query, db_file)

# 각 Region 별 상위 5개의 Country GDP Average SELECT
def get_average_gdp_by_region():
    query = """
        SELECT Region, ROUND(AVG(GDP_USD_billion),2)
        FROM
            (
            SELECT Region, GDP_USD_billion, RANK() OVER (PARTITION BY Region ORDER BY GDP_USD_billion DESC) AS RANKING
            FROM Total_Countries_by_GDP
            )
        WHERE RANKING <= 5 AND Region IS NOT NULL
        GROUP BY Region
        """
    return get_sql(query, db_file)

# 디렉토리가 존재하지 않는 경우 생성 및 파일 생성
if not os.path.exists(db_file):
    open(db_file, 'w').close()

# ETL 을 합친 함수
def etl_process():
    log("ETL process started")
    df = extract_data(GDP_WIKIPEDIA_URL) # Extract process 실행
    df = transform_data(df) # Transform process 실행
    load_data(df, db_file) # Load process 실행
    log("ETL process completed")

def wait_until_next_hour(): # 지금은 정각마다 DB에 저장하는 코드를 작성했다.
                            # 정각마다 DB에 잘 저장하는 것을 확인했으므로 주기를 분기 또는 반기 단위로 하여 GDP를 update하면 된다.
    now = datetime.now()
    # 다음 정각 계산
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    # 다음 정각까지의 시간을 계산하여 불필요한 Overhead가 발생하지 않게 sleep하도록 하였다.
    wait_seconds = (next_hour - now).total_seconds()
    time.sleep(wait_seconds)

# 받은 query를 출력
def Print_get_100B():
    over_100b = get_OVER_100b()
    print("Countries with GDP over 100B:")
    for row in over_100b:
        print(row)
    print()
def Print_get_TOP5_by_region():
    avg_gdp_by_region = get_average_gdp_by_region()
    print("\nAverage GDP by region for top 5 countries:")
    for row in avg_gdp_by_region:
        print(row)
    print()

if __name__ == "__main__":
    # ETL 프로세스를 첫 실행
    etl_process()
    # 데이터베이스 쿼리 결과 출력
    Print_get_100B()
    Print_get_TOP5_by_region()
    
    # 정각마다 ETL 프로세스를 실행
    while True:
        wait_until_next_hour() # 다음 정각을 계산하고 남는 시간만큼 sleep한다.
        etl_process() # ETL process를 진행한다.
        # 데이터베이스 쿼리 결과 출력
        Print_get_100B()
        Print_get_TOP5_by_region()
