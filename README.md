TWS BookTrader replacement to Quickly Change between instruments and place Trades for US stocks/options  
USE THIS SCRIPT AT YOUR OWN RISK!!  
msg me about bugs/issues on godel  
-schwab99  
<img width="202" height="634" alt="stock" src="https://github.com/user-attachments/assets/95519f04-884a-48d9-bbda-3335fd873cb6" />
<img width="202" height="634" alt="option" src="https://github.com/user-attachments/assets/c0265deb-2212-4c71-a3dd-f8da32991589" />
<img width="202" height="634" alt="delete" src="https://github.com/user-attachments/assets/cf9c5935-6ebd-4970-86d6-d901627a9ee1" />  
pip install pyqt6 [pywin32 OR comment out WINDOWS]  
tws api install instructions: https://www.interactivebrokers.com/campus/trading-lessons/accessing-the-tws-python-api-source-code/  
port 7497 (search for 'init tws api')

### main nav
left-click left: buy  
right-click right: sell  
ctrl+left-click: stop mkt  
mouse scroll on prices: zoom  
mouse scroll on ladder/J/K: scroll  
M: snap to mid  
F: snap to mid at default zoom level  
X: focus and clear size box  
click right panel/D: toggle cancel-only mode  
E: enter instrument panel  
O: options panel  
B/click top left arrow: go back to underlying  
T: tools panel  
H: order error diag panel  
C: one cancels all order panel  
V: price trigger order panel  
click on order: cancel  
ctrl+click on order: force remove

### options panel
left-click/1-5: select option  
right-click option: save it "*" on exit  
E: edit mode  
left-click on option in edit mode: select  
up/down: move selected option

### enter inst panel
middle mouse: paste  
return: submit  
up/down: history  
/ES etc for minis or OCC for options  
ESC/return sometimes/click outside panel: close most panels

also AHK V2 script to move instrument in chart under mouse with ctrl+t  
good for chart trading or to move lots of options over.  
requires https://github.com/TheArkive/Socket_ahk2
