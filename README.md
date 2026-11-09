# Opener (meteo.kg)

Автоматизированный запуск и настройка карты погоды meteo.kg на базе Selenium WebDriver (Windows 10/11).

## Возможности

- **Самовосстановление (Self-Healing):** автоматическое определение установленной версии Google Chrome (включая Chrome 115+, 120+, 157+) и динамическая загрузка соответствующего ChromeDriver из Google Chrome for Testing (CFT).
- **Слой фактической погоды:** автоматический переход на слой `actual-weather` с валидацией состояния.
- **Точный зум:** применение целевого CSS transform к прокси-элементу карты.
- **Гарантированный полноэкранный режим (Fullscreen):**
  - Поддержка нативного HTML5 Fullscreen через Chrome DevTools Protocol (`Runtime.evaluate` с `userGesture: True`).
  - Программный псевдо-полноэкранный режим Leaflet (`map._enablePseudoFullscreen` / `leaflet-pseudo-fullscreen`) на случай блокировок браузера.
  - Полноэкранный режим окна браузера (F11) и закрытие по нажатию клавиши `Escape`.
- **Шаговый зум:** колесико мыши / Leaflet zoom controls.
- **Сборка в единый исполняемый файл (.exe):** полная упаковка зависимостей через PyInstaller.

## Запуск

### Из исходного кода:
```bash
python open.py
```
или через батник:
```cmd
run_opener.bat
```

### Сборка EXE:
Запустите `build_exe.bat`, после чего готовый исполняемый файл `opener.exe` появится в папке `dist/`.

## Релизы

Готовые сборки `opener.exe` доступны на вкладке [Releases](https://github.com/Belli666/opener/releases).
