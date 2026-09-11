from pathlib import Path

p = Path("login.html")
t = p.read_text(encoding="utf-8")

if "lisan_workspace" in t:
    print("already patched")
else:
    old = "location.replace(\"/app\");"
    new = ('try{ Object.keys(localStorage).forEach(function(k){ if(k.indexOf("lisan_workspace")===0) localStorage.removeItem(k); }); }catch(e){}\n'
           '    location.replace("/app");')
    if old in t:
        t = t.replace(old, new, 1)
        p.write_text(t, encoding="utf-8")
        print("patched: successful login now clears the saved workspace")
    else:
        print("WARNING: finishLogin redirect line not found - paste the finishLogin function")