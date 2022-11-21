.PHONY: install

all: bottle.py
	cp nightlies.service.example nightlies.service
	cp nightlies.timer.example nightlies.timer

bottle.py:
	curl https://raw.githubusercontent.com/bottlepy/bottle/0.12.23/bottle.py -O

