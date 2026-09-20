@echo off
setlocal
set "BASE=%MUSICSERVER_BASE%"
cd /d "%BASE%"
python "%BASE%\tools\local_library.py" >> "%BASE%\tools\automation.log" 2>&1
python "%BASE%\tools\listenbrainz_recommendations.py" >> "%BASE%\tools\automation.log" 2>&1
python "%BASE%\tools\listenbrainz_import_queue.py" >> "%BASE%\tools\automation.log" 2>&1
python "%BASE%\tools\spotify_dj_recommender.py" >> "%BASE%\tools\automation.log" 2>&1
"%BASE%\navidrome.exe" scan --configfile "%BASE%\navidrome.toml" --nobanner --target "1:Playlists" >> "%BASE%\tools\automation.log" 2>&1
endlocal
