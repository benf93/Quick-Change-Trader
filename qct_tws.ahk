; v1.0 
#Requires AutoHotkey >=2.0
SetControlDelay 0
SetKeyDelay -1
CoordMode "Mouse", "Screen"
CoordMode "Pixel", "Screen"
#Include _socket.ahk
#Warn All, Off
#SingleInstance Force

cb(sock, event, err) {
	MsgBox "socket error!"
}

send_to_socket(msg) {
	sock := winsock("client", cb, "IPV4")

	If !(sock.Connect("127.0.0.1", 3002, true))
			return

	strbuf := Buffer(StrLen(msg))
	StrPut(msg, strbuf, "UTF-8")

	sock.Send(strbuf)
	sock.Close()
}

; transfer chart instrument to QCT based on chart title 
$^t:: {
	MouseGetPos &mx, &my, &window	
	WinExist(window)
	t := WinGetTitle()
	if not ( InStr(t, "bars") ) {
		Send StrReplace(A_ThisHotkey, "$")
		return
	}
	
	MouseClick
	Sleep 100
	t := WinGetTitle()
	
	opt_dir := "N"
	if (InStr(t, " C")) {
		t := StrSplit(t, "Call")
		opt_dir := "C"
	} else if (InStr(t, " P")) {
		t := StrSplit(t, "Put")
		opt_dir := "P"
	}

	if ( opt_dir != "N" ) {
		months := [ "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC" ] 
		t := StrSplit(t[1], " ")
		sym 	:= t[1]
		month := t[2]
		Loop months.Length 
			if ( months[A_Index] = month ) {
				if ( A_Index < 10 ) 
					month := "0" A_index
				else
					month := A_index
				break
			}
		day 	:= t[3]
		year 	:= SubStr(t[4], 2)
		price := t[5]

		pint  := Integer(price * 1000)

		; sym padded to 6 with spaces, price x1000 padded with 0 to 8 digits
		occ := Format("{:-6}",sym) year month day opt_dir Format("{:08}", pint)
		send_to_socket(occ)

		return
	}
		
	t := StrSplit(t, "@")

	str := t[1] ",EQ" 

	send_to_socket(t[1])
}
