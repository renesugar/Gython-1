#!/usr/bin/env python
# PythonJS to CoffeeScript Translator
# by Brett Hartshorn - copyright 2014
# License: "New BSD"
import os, sys
import ast
import pythonjs

class TransformSuperCalls( ast.NodeVisitor ):
	def __init__(self, node, class_names):
		self._class_names = class_names
		self.visit(node)

	def visit_Call(self, node):
		if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in self._class_names:
			node.func.attr = '__' + node.func.attr

class CollectNames(ast.NodeVisitor):
	def __init__(self):
		self._names = []
	def visit_Name(self, node):
		self._names.append( node )

def collect_names(node):
	a = CollectNames()
	a.visit( node )
	return a._names


class LuaGenerator( pythonjs.JSGenerator ):
	_classes = dict()
	_class_props = dict()

	def _visit_subscript_ellipsis(self, node):
		name = self.visit(node.value)
		return '%s.__wrapped__' %name

	def visit_Name(self, node):
		if node.id == 'None':
			return 'nil'
		elif node.id == 'True':
			return 'true'
		elif node.id == 'False':
			return 'false'
		elif node.id == 'null':
			return 'nil'
		return node.id

	def visit_And(self, node):
		return ' and '

	def visit_Or(self, node):
		return ' or '


	def visit_Subscript(self, node):
		if isinstance(node.slice, ast.Ellipsis):
			return self._visit_subscript_ellipsis( node )
		else:
			return '%s[%s]' % (self.visit(node.value), self.visit(node.slice))

	def _visit_call_helper_JSObject(self, node):
		if node.keywords:
			kwargs = map(self.visit, node.keywords)
			f = lambda x: '%s = %s' % (x[0], x[1])
			out = ', '.join(map(f, kwargs))
			return '{%s}' % out
		else:
			return '{}'

	def _visit_call_helper_JSArray(self, node):
		if node.args:
			args = map(self.visit, node.args)
			out = ', '.join(args)
			return '{%s}' % out
		else:
			return '{}'

	def _visit_call_helper_var(self, node):
		args = [ self.visit(a) for a in node.args ]
		if self._function_stack:
			fnode = self._function_stack[-1]
			rem = []
			for arg in args:
				if arg in fnode._local_vars:
					rem.append( arg )
				else:
					fnode._local_vars.add( arg )
			for arg in rem:
				args.remove( arg )

		if args:
			out = ', '.join(args)
			return 'local %s' % out
		else:
			return ''

	def _inline_code_helper(self, s):
		return s

	def visit_While(self, node):
		body = [ 'while %s do' %self.visit(node.test)]
		self.push()
		for line in list( map(self.visit, node.body) ):
			body.append( self.indent()+line )
		body.append( self.indent() + 'end' )
		self.pull()
		return '\n'.join( body )



	def visit_Pass(self, node):
		return '--pass--'

	def visit_If(self, node):
		out = []
		out.append( 'if %s then' %self.visit(node.test) )
		self.push()

		for line in list(map(self.visit, node.body)):
			out.append( self.indent() + line )

		orelse = []
		for line in list(map(self.visit, node.orelse)):
			orelse.append( self.indent() + line )

		self.pull()

		if orelse:
			out.append( self.indent() + 'else')
			out.extend( orelse )

		out.append( self.indent() + 'end')


		return '\n'.join( out )


	def visit_List(self, node):
		## note, arrays in lua start at index 1, not zero!
		return '{%s}' % ', '.join(map(self.visit, node.elts))

	def visit_Dict(self, node):
		a = []
		for i in range( len(node.keys) ):
			k = self.visit( node.keys[ i ] )
			v = self.visit( node.values[i] )
			a.append( '%s=%s'%(k,v) )
		b = ','.join( a )
		return '{%s}' %b

	def visit_ClassDef(self, node):
		raise NotImplementedError

	def visit_For(self, node):
		if isinstance(node.target, ast.Name):
			a = ['for __i,%s in pairs(%s) do' %(self.visit(node.target), self.visit(node.iter))]
		elif isinstance(node.target, ast.List):
			x = ','.join([self.visit(elt) for elt in node.target.elts])
			a = ['for %s in %s do' %(x, self.visit(node.iter))]
		else:
			raise SyntaxError( node.target )

		for n in node.body:
			a.append( self.visit(n) )
		a.append('end')
		return '\n'.join(a)

	def visit_Expr(self, node):
		return self.visit(node.value)


	def visit_Print(self, node):
		args = [self.visit(e) for e in node.values]
		return 'print(%s)' % ', '.join(args)


	def visit_Assign(self, node):
		assert len(node.targets) == 1
		target = node.targets[0]
		if isinstance(target, ast.Tuple):
			raise NotImplementedError
		else:
			target = self.visit(target)
			value = self.visit(node.value)
			code = '%s = %s;' % (target, value)
			return code

	def _visit_function(self, node):
		getter = False
		setter = False
		klass = None
		for decor in node.decorator_list:
			if isinstance(decor, ast.Name) and decor.id == 'property':
				getter = True
			elif isinstance(decor, ast.Attribute) and isinstance(decor.value, ast.Name) and decor.attr == 'setter':
				setter = True
			elif isinstance(decor, ast.Attribute) and isinstance(decor.value, ast.Name) and decor.attr == 'prototype':
				klass = self.visit(decor)
			else:
				raise SyntaxError(decor)

		args = []  #self.visit(node.args)
		oargs = []
		offset = len(node.args.args) - len(node.args.defaults)
		varargs = False
		varargs_name = None
		for i, arg in enumerate(node.args.args):
			a = arg.id
			dindex = i - offset

			if dindex >= 0 and node.args.defaults:
				default_value = self.visit( node.args.defaults[dindex] )
				oargs.append( '%s=%s' %(a, default_value) )
			else:
				args.append( a )

		if oargs:
			args.extend( ','.join(oargs) )

		buffer = self.indent()
		if hasattr(node,'_prefix'): buffer += node._prefix + ' '

		#if getter:
		#	buffer += 'get %s {\n' % node.name
		#elif setter:
		#	buffer += 'set %s(%s) {\n' % (node.name, ', '.join(args))
		#else:
		if klass:
			buffer += '%s.%s = function(%s)\n' % (klass, node.name, ', '.join(args))
		else:
			buffer += '%s = function(%s)\n' % (node.name, ', '.join(args))
		self.push()

		#if varargs:
		#	buffer += 'var %s = new list([]);\n' %varargs_name
		#	for i,n in enumerate(varargs):
		#		buffer += 'if (%s != null) %s.append(%s);\n' %(n, varargs_name, n)

		body = list()
		for child in node.body:
			if isinstance(child, ast.Str):
				continue
			else:
				body.append( self.indent() + self.visit(child) )

		body.append( '' )
		buffer += '\n'.join(body)
		self.pull()
		buffer += self.indent() + 'end'

		return buffer


	def visit_Is(self, node):
		return ' is '

	def _visit_call_helper_instanceof(self, node):
		args = map(self.visit, node.args)
		if len(args) == 2:
			if args[1] == 'Number':
				args[1] = 'num'
			return '%s is %s' %tuple(args)
		else:
			raise SyntaxError( args )



def main(script):
	tree = ast.parse(script)
	return LuaGenerator().visit(tree)


def command():
	scripts = []
	if len(sys.argv) > 1:
		for arg in sys.argv[1:]:
			if arg.endswith('.py'):
				scripts.append( arg )

	if len(scripts):
		a = []
		for script in scripts:
			a.append( open(script, 'rb').read() )
		data = '\n'.join( a )
	else:
		data = sys.stdin.read()

	lua = main( data )
	print( lua )


if __name__ == '__main__':
	command()
