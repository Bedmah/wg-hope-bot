# WG Bot - Docker + CI CD

Публичный репозиторий без секретов. Все реальные значения храним в .env на сервере или в GitHub Secrets.

## Что в репозитории
- Dockerfile, docker-compose.yml, .env.example
- src код бота
- requirements.txt
- GitHub Actions workflow

## Что не кладем в репозиторий
- .env с реальными значениями
- конфиги типа amn.conf
- реальные IP, токены, tg id

## Первый запуск на сервере
1. Установить Docker и compose.
2. Подготовить каталог:
```
sudo mkdir -p /opt/wg-bot/data/clients
sudo chown -R $USER:$USER /opt/wg-bot
```
3. Скопировать docker-compose.yml и .env.example, создать .env и заполнить плейсхолдеры.
4. Запуск:
```
docker compose up -d
docker logs -f wg-bot
```
