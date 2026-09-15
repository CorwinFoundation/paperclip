import importlib.util,json,subprocess,sys,zipapp
from pathlib import Path
R=Path(__file__).parents[1];s=importlib.util.spec_from_file_location("backbond_count",R/"src/backbond_count.py");m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def test_n5(capsysbinary):
 out=[]
 for _ in range(5):assert m.main(["--self-check"])==0;out.append(capsysbinary.readouterr().out)
 assert len(set(out))==1
def test_show_no_write(tmp_path,capsysbinary):
 p=tmp_path/"in";p.write_text(json.dumps({"messages":[{"role":"user","content":"x"},{"role":"assistant","content":"y"}]})+"\n");o=tmp_path/"out";assert m.main([str(p),"--output",str(o),"--show-what-leaves"])==0;assert not o.exists();assert json.loads(capsysbinary.readouterr().out)["schema_version"]=="vendor-count-v0.1"
def test_unknown(tmp_path,capsys):
 p=tmp_path/"bad";p.write_text('{"wat":1}\n');assert m.main([str(p)])==2;assert "generic OpenAI" in capsys.readouterr().err
def test_zipapp_self_check(tmp_path):
 app=tmp_path/"app";app.mkdir();(app/"backbond_count.py").write_bytes((R/"src/backbond_count.py").read_bytes());(app/"__main__.py").write_text("from backbond_count import main\nraise SystemExit(main())\n")
 target=tmp_path/"backbond-count.pyz";zipapp.create_archive(app,target=target,interpreter="/usr/bin/env python3")
 run=subprocess.run([sys.executable,str(target),"--self-check"],capture_output=True,text=True,check=False)
 assert run.returncode==0,run.stderr;assert json.loads(run.stdout)["fixture_check"]["matched"] is True
