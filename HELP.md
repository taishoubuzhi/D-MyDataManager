# 资源文件 resource.qrc编译为 resource.py
```cmd
pyside6-rcc src\app\resource\resource.qrc -o src\app\resource\resource.py
```
完成编译后，将resource.py文件的
```python
from PySide6 import QtCore
```
替换为
```python
from PyQt6 import QtCore
```
# 翻译文件 app.zh_CN.ts编译为 app.zh_CN.qm 
```cmd
pyside6-lrelease src\app\resource\i18n\app.zh_CN.ts -qm src\app\resource\i18n\app.zh_CN.qm             
```
```cmd
pyside6-lrelease src\app\resource\i18n\app.en.ts -qm src\app\resource\i18n\app.en.qm             
```